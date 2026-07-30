"""AWS Signature Version 4 request signing (stdlib-only).

SigV4 is a stable, publicly specified request-signing algorithm. Implementing it
here on ``hashlib``/``hmac`` avoids pulling ``botocore`` in for one signer, and
keeps signing inside the process (no sidecar). Only the ``cryptography`` package
is arch-restricted to ``shared/crypto/encryption.py``; SigV4 needs neither it nor
any third-party dependency.

The signer produces the headers to merge onto an outbound request:
``authorization``, ``x-amz-date``, ``x-amz-content-sha256`` — plus
``x-amz-security-token`` when temporary (session-token) credentials are used. We
sign a **minimal** header set (``host``, ``x-amz-date``, ``x-amz-content-sha256``,
and ``x-amz-security-token`` when present) so a header added *after* signing (e.g.
the runner's ``accept-encoding: identity``) never invalidates the signature.

References: AWS "Signing AWS API requests" (SigV4), the canonical-request /
string-to-sign / signing-key derivation.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from posixpath import normpath
from urllib.parse import quote, unquote, urlsplit

_ALGORITHM = "AWS4-HMAC-SHA256"
_AWS4_REQUEST = "aws4_request"
# sha256 of the empty byte string — the payload hash for a body-less request.
_EMPTY_PAYLOAD_HASH = hashlib.sha256(b"").hexdigest()
_DEFAULT_PORTS = {"http": "80", "https": "443"}
# S3 is the one service that single-encodes the canonical path and does not
# normalise it (object keys are opaque and may legitimately contain ``.``/``..``
# and ``//``). Every other service double-encodes and normalises. See the AWS
# SigV4 spec, "Create a canonical request", CanonicalURI.
_S3_SERVICES = frozenset({"s3", "s3-object-lambda"})


@dataclass(frozen=True, slots=True)
class SigV4Material:
    """Decrypted inputs to the signer.

    Carries **decrypted** secret material at request time; never logged, never
    persisted post-decrypt. ``__repr__`` elides the secrets so a stray
    ``repr()`` (e.g. of an enclosing ``RunnerRequest``) cannot leak them.
    """

    access_key_id: str
    secret_access_key: str
    region: str
    service: str
    session_token: str | None = None

    def __repr__(self) -> str:
        # Redaction backstop: never render the secret or session token.
        return (
            f"SigV4Material(access_key_id={self.access_key_id!r}, "
            f"region={self.region!r}, service={self.service!r}, "
            f"secret_access_key='***', "
            f"session_token={'***' if self.session_token else None})"
        )


def _uri_encode(value: str, *, is_path: bool) -> str:
    """RFC 3986 percent-encoding per the SigV4 spec.

    Unreserved chars (``A-Za-z0-9-_.~``) are never encoded; everything else is
    percent-encoded. In the path, ``/`` is preserved as a segment separator.
    """
    safe = "/" if is_path else ""
    return quote(value, safe=safe + "-_.~")


def _normalize_path(path: str) -> str:
    """RFC 3986 ``remove_dot_segments`` (``.``/``..``/``//``), preserving trailing ``/``.

    AWS's verifier normalises the canonical path for every service except S3, so
    the signer must too. ``posixpath.normpath`` does the segment resolution but
    drops a trailing slash that RFC 3986 keeps: a path ending in ``/``, ``/.`` or
    ``/..`` normalises to a directory (trailing slash), e.g. ``/foo/bar/..`` ->
    ``/foo/``. We restore that slash to match AWS (and Java's ``URI.normalize``).
    """
    if not path:
        return "/"
    normalized = normpath(path)
    keeps_trailing_slash = path.endswith(("/", "/.", "/.."))
    if keeps_trailing_slash and not normalized.endswith("/"):
        normalized += "/"
    # POSIX ``normpath`` deliberately preserves a leading ``//`` (implementation-
    # defined per POSIX); AWS collapses it, so squeeze any leading run to one.
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    # normpath can return "." for an empty/relative path.
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _canonical_uri(path: str, *, is_s3: bool) -> str:
    """CanonicalURI for the SigV4 canonical request.

    ``path`` is the **decoded** absolute path component (what ``urlsplit`` yields
    and what AWS SDKs canonicalise from — they build both the wire request and the
    signature from the same decoded path). For non-S3 services the path is
    RFC 3986-normalised then URI-encoded **twice**; for S3 it is encoded **once**
    and never normalised (object keys are opaque and ``.``/``..``/``//`` are
    significant). Verified byte-for-byte against the official
    ``aws-sig-v4-test-suite`` ``get-space``/``get-utf8``/``normalize-path`` vectors.
    """
    if not path:
        return "/"
    if is_s3:
        return _uri_encode(path, is_path=True)
    normalized = _normalize_path(path)
    return _uri_encode(_uri_encode(normalized, is_path=True), is_path=True)


def _canonical_query(query: str) -> str:
    """CanonicalQueryString from the raw query string.

    We split on ``&``/``=`` and ``unquote`` (never ``unquote_plus``) so a literal
    ``+`` is signed as ``+`` — decoding ``+`` to a space (what ``parse_qsl`` does)
    would sign a value the target never received. Each name/value is re-encoded
    and the pairs sorted by (key, value). Verified against the official
    ``aws-sig-v4-test-suite`` query vectors.
    """
    if not query:
        return ""
    pairs: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        # ``partition`` gives an empty value for a bare ``?flag`` and for ``?flag=``
        # alike, so both canonicalise to ``flag=`` — matching AWS's rule that a
        # value-less parameter still emits an explicit ``=``.
        pairs.append(
            (
                _uri_encode(unquote(key), is_path=False),
                _uri_encode(unquote(value), is_path=False),
            )
        )
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def sign_request(
    *,
    method: str,
    url: str,
    body: bytes | None,
    material: SigV4Material,
    now: datetime | None = None,
) -> dict[str, str]:
    """Return the SigV4 headers to merge onto the outbound request.

    ``now`` is injectable so tests can pin the timestamp against the official AWS
    test vectors; production passes ``None`` (current UTC).
    """
    ts = (now or datetime.now(UTC)).astimezone(UTC)
    amz_date = ts.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = ts.strftime("%Y%m%d")

    split = urlsplit(url)
    host = split.hostname or ""
    if split.port is not None and str(split.port) != _DEFAULT_PORTS.get(split.scheme, ""):
        host = f"{host}:{split.port}"

    payload_hash = hashlib.sha256(body).hexdigest() if body else _EMPTY_PAYLOAD_HASH

    # Signed header set — minimal and deterministic (already sorted by name).
    signed: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if material.session_token:
        signed["x-amz-security-token"] = material.session_token

    signed_header_names = ";".join(sorted(signed))
    canonical_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))

    is_s3 = material.service.lower() in _S3_SERVICES
    canonical_request = "\n".join(
        [
            method.upper(),
            _canonical_uri(split.path, is_s3=is_s3),
            _canonical_query(split.query),
            canonical_headers,
            signed_header_names,
            payload_hash,
        ]
    )

    scope = f"{date_stamp}/{material.region}/{material.service}/{_AWS4_REQUEST}"
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    signing_key = _derive_signing_key(
        secret_access_key=material.secret_access_key,
        date_stamp=date_stamp,
        region=material.region,
        service=material.service,
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    authorization = (
        f"{_ALGORITHM} "
        f"Credential={material.access_key_id}/{scope}, "
        f"SignedHeaders={signed_header_names}, "
        f"Signature={signature}"
    )

    out = {
        "authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    if material.session_token:
        out["x-amz-security-token"] = material.session_token
    return out


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _derive_signing_key(
    *, secret_access_key: str, date_stamp: str, region: str, service: str
) -> bytes:
    """The chained-HMAC signing-key derivation: date → region → service → request."""
    k_date = _hmac(f"AWS4{secret_access_key}".encode(), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, _AWS4_REQUEST)
