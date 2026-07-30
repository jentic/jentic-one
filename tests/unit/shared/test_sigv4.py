"""SigV4 signer tests — pinned against the official AWS SigV4 test suite.

The canonicalisation edge cases (path encoding/normalisation, query encoding and
ordering) are the dark corners where SigV4 implementations break, so they are
asserted **byte-for-byte** against the published ``aws-sig-v4-test-suite``
CanonicalURI/CanonicalQueryString vectors — an oracle that is independent of the
code under test. The end-to-end signature is separately pinned to the AWS
"get-vanilla" worked example's documented signature.

Fixed credential/timestamp used by the suite:

- access key ``AKIDEXAMPLE``
- secret ``wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY``
- region ``us-east-1``, service ``service``
- date ``20150830T123600Z``
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest

from jentic_one.shared.aws.sigv4 import (
    SigV4Material,
    _canonical_query,
    _canonical_uri,
    sign_request,
)

_FIXED_NOW = datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)
# AWS-documented get-vanilla signature (public test vector, not a real secret).
_GET_VANILLA_SIG = "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
_MATERIAL = SigV4Material(
    access_key_id="AKIDEXAMPLE",
    secret_access_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
    region="us-east-1",
    service="service",
)


# ---------------------------------------------------------------------------
# Canonicalisation — pinned byte-for-byte against the official test suite.
# Each row is (raw decoded path, expected non-S3 CanonicalURI). These are the
# CanonicalURI lines from get-vanilla, get-space, get-utf8 and the normalize-path
# family of the aws-sig-v4-test-suite (service='service', which double-encodes
# and normalises).
# ---------------------------------------------------------------------------
_CANONICAL_URI_VECTORS = [
    ("/", "/"),
    ("/example space/", "/example%2520space/"),  # get-space
    ("/\u1234", "/%25E1%2588%25B4"),  # get-utf8
    ("//", "/"),  # normalize-path.get-slash
    ("//example//", "/example/"),  # normalize-path.get-slashes
    ("/./", "/"),  # normalize-path.get-slash-dot-slash
    ("/./foo", "/foo"),  # normalize-path.get-slash-pointless-dot
    ("/foo/bar/..", "/foo/"),  # normalize-path.get-relative
    ("/foo/bar/../..", "/"),  # normalize-path.get-relative-relative
]


@pytest.mark.parametrize(("path", "expected"), _CANONICAL_URI_VECTORS)
def test_canonical_uri_matches_official_vectors(path: str, expected: str) -> None:
    assert _canonical_uri(path, is_s3=False) == expected


def test_canonical_uri_s3_single_encodes_and_skips_normalization() -> None:
    # S3 object keys are opaque: encode once, and never collapse ``//`` or ``..``
    # (an object literally named ``a//b`` must sign as ``a//b``, not ``a/b``).
    assert _canonical_uri("/bucket/a b", is_s3=True) == "/bucket/a%20b"
    assert _canonical_uri("/my-object//example//photo", is_s3=True) == (
        "/my-object//example//photo"
    )


# (raw query string, expected CanonicalQueryString).
_CANONICAL_QUERY_VECTORS = [
    ("Param1=value1", "Param1=value1"),  # get-vanilla-query
    ("Param2=value2&Param1=value1", "Param1=value1&Param2=value2"),  # order-key-case
    ("-._~=-._~", "-._~=-._~"),  # get-vanilla-query-unreserved (untouched)
    ("\u1234=bar", "%E1%88%B4=bar"),  # utf8 key
    # A literal ``+`` must survive as ``%2B`` (unquote, not unquote_plus): decoding
    # it to a space would sign a value the target never received.
    ("p=a+b", "p=a%2Bb"),
    # Value-less parameter still emits an explicit ``=`` (AWS ``?acl`` rule).
    ("acl", "acl="),
    ("flag=", "flag="),
]


@pytest.mark.parametrize(("query", "expected"), _CANONICAL_QUERY_VECTORS)
def test_canonical_query_matches_official_vectors(query: str, expected: str) -> None:
    assert _canonical_query(query) == expected


# ---------------------------------------------------------------------------
# End-to-end signature.
# ---------------------------------------------------------------------------
def _get_vanilla_reference_signature() -> str:
    """Recompute the get-vanilla signature from scratch (independent HMAC chain).

    This oracle canonicalises the *simplest* possible request (``/`` path, empty
    query, host+x-amz-date only) so it shares none of the encoding logic that the
    parametrised vectors above already pin. It exists only to cross-check the raw
    signing-key derivation against the AWS-documented worked example.
    """
    canonical_request = "\n".join(
        [
            "GET",
            "/",
            "",
            "host:example.amazonaws.com\nx-amz-date:20150830T123600Z\n",
            "host;x-amz-date",
            hashlib.sha256(b"").hexdigest(),
        ]
    )
    sts = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            "20150830T123600Z",
            "20150830/us-east-1/service/aws4_request",
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = _h(f"AWS4{_MATERIAL.secret_access_key}".encode(), "20150830")
    k = _h(k, "us-east-1")
    k = _h(k, "service")
    k = _h(k, "aws4_request")
    return hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()


def test_signing_key_chain_matches_aws_get_vanilla() -> None:
    # Anchor the signing-key derivation to AWS's published get-vanilla signature.
    assert _get_vanilla_reference_signature() == _GET_VANILLA_SIG


def test_sign_request_get_vanilla_authorization() -> None:
    # The production signer additionally signs x-amz-content-sha256 (required by
    # aoss/S3 and many other services), so the SignedHeaders set differs from the
    # bare get-vanilla vector — recompute the expected signature over that set.
    empty_hash = hashlib.sha256(b"").hexdigest()
    canonical_request = "\n".join(
        [
            "GET",
            "/",
            "",
            (
                "host:example.amazonaws.com\n"
                f"x-amz-content-sha256:{empty_hash}\n"
                "x-amz-date:20150830T123600Z\n"
            ),
            "host;x-amz-content-sha256;x-amz-date",
            empty_hash,
        ]
    )
    sts = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            "20150830T123600Z",
            "20150830/us-east-1/service/aws4_request",
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = _h(f"AWS4{_MATERIAL.secret_access_key}".encode(), "20150830")
    k = _h(k, "us-east-1")
    k = _h(k, "service")
    k = _h(k, "aws4_request")
    expected = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()

    headers = sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        f"Signature={expected}"
    )
    assert headers["x-amz-date"] == "20150830T123600Z"
    assert headers["x-amz-content-sha256"] == empty_hash


def test_get_vanilla_query_order_is_normalized() -> None:
    # Same signature regardless of the incoming query-param order (AWS sorts them).
    a = sign_request(
        method="GET",
        url="https://example.amazonaws.com/?Param2=value2&Param1=value1",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    b = sign_request(
        method="GET",
        url="https://example.amazonaws.com/?Param1=value1&Param2=value2",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    assert a["authorization"] == b["authorization"]


def test_post_with_body_hashes_payload() -> None:
    headers = sign_request(
        method="POST",
        url="https://example.amazonaws.com/",
        body=b'{"q": "search"}',
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    # Non-empty payload → not the empty-body hash.
    assert headers["x-amz-content-sha256"] != hashlib.sha256(b"").hexdigest()
    assert "x-amz-content-sha256" in headers["authorization"].split("SignedHeaders=")[1]


def test_s3_and_non_s3_sign_the_same_path_differently() -> None:
    # A concrete regression guard for the encode-once-vs-twice rule: the same URL
    # with a space in the path must sign differently for S3 vs a normalising
    # service, because S3 single-encodes and everyone else double-encodes.
    url = "https://example.amazonaws.com/a b/c"
    s3 = sign_request(
        method="GET",
        url=url,
        body=None,
        material=SigV4Material(
            access_key_id="AKIDEXAMPLE",
            secret_access_key=_MATERIAL.secret_access_key,
            region="us-east-1",
            service="s3",
        ),
        now=_FIXED_NOW,
    )
    other = sign_request(method="GET", url=url, body=None, material=_MATERIAL, now=_FIXED_NOW)
    assert s3["authorization"] != other["authorization"]


def test_session_token_adds_header_and_is_signed() -> None:
    material = SigV4Material(
        access_key_id="AKIDEXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
        region="us-east-1",
        service="aoss",
        session_token="FQoGZXIvYXdzEXAMPLETOKEN",
    )
    headers = sign_request(
        method="GET",
        url="https://collection.us-east-1.aoss.amazonaws.com/_search",
        body=None,
        material=material,
        now=_FIXED_NOW,
    )
    assert headers["x-amz-security-token"] == "FQoGZXIvYXdzEXAMPLETOKEN"
    signed = headers["authorization"].split("SignedHeaders=")[1].split(",")[0]
    assert "x-amz-security-token" in signed


def test_non_default_port_kept_in_host() -> None:
    default = sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    ported = sign_request(
        method="GET",
        url="https://example.amazonaws.com:8443/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    # Host is part of the signature, so a non-default port changes it.
    assert default["authorization"] != ported["authorization"]


def test_repr_elides_secret_and_session_token() -> None:
    material = SigV4Material(
        access_key_id="AKIDEXAMPLE",
        secret_access_key="super-secret",  # pragma: allowlist secret
        region="us-east-1",
        service="aoss",
        session_token="Zx9SESSIONVALUE",
    )
    rendered = repr(material)
    assert "super-secret" not in rendered
    assert "Zx9SESSIONVALUE" not in rendered
    assert "AKIDEXAMPLE" in rendered


def test_secret_never_appears_in_output_headers() -> None:
    headers = sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    assert _MATERIAL.secret_access_key not in "".join(headers.values())
