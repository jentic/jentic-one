"""HTTP fetch helper for the catalog slice — SSRF-guarded GET of JSON documents and spec bytes.

A thin, redirect-safe wrapper around ``httpx.AsyncClient`` that reuses the same
``validate_upstream_url_async`` SSRF guard and ingest-config knobs (timeout, size cap,
redirect budget) as the import fetch layer. It exists so the catalog sources can
pull an upstream index and individual specs without duplicating the redirect/size
hardening and without coupling to the ingest pipeline's spec-shaped return type.

Callers may pass a long-lived ``client`` (a catalog source's pooled client); it
must be built with ``follow_redirects=False`` over the egress-pinned transport,
because every redirect hop is re-validated here. Without one, each call opens
and closes its own client. Bodies are read as a stream and abandoned as soon as
they pass ``max_spec_bytes``.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from jentic_one.shared.config import IngestConfig
from jentic_one.shared.egress import build_pinned_transport
from jentic_one.shared.url_validation import validate_upstream_url_async, with_default_scheme

#: Identify the catalog fetcher to upstream hosts. Server-initiated recurring
#: third-party fetches announce themselves so operators can attribute traffic
#: and hosts can apply sane rate limits (unidentified bots are throttled first).
CATALOG_USER_AGENT = "jentic-one-catalog/1 (+https://github.com/jentic/jentic-one)"

#: The longest ``Retry-After`` honoured; a larger (or far-future) value is clamped.
MAX_RETRY_AFTER_SECONDS = 300.0

#: Upstream statuses whose ``Retry-After`` is read.
_RETRY_AFTER_STATUSES = frozenset({429, 503})


class CatalogFetchError(Exception):
    """Raised when an upstream catalog document cannot be fetched or parsed.

    ``status_code`` carries the upstream HTTP status when the failure was a
    non-success response, and is ``None`` for transport, guard and parse failures.
    ``retry_after_s`` is the upstream's ``Retry-After`` on a ``429``/``503``
    (clamped to ``[0, MAX_RETRY_AFTER_SECONDS]``), else ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class ConditionalFetch:
    """Result of a conditional (``If-None-Match``) byte fetch.

    ``not_modified`` is ``True`` when the server answered ``304`` to our
    ``If-None-Match`` — ``content``/``digest`` are then ``None`` (nothing was
    transferred). Otherwise ``content`` holds the raw body, ``digest`` its
    ``sha256`` hex, and ``etag`` the response's validator (``None`` when the
    upstream sends none).
    """

    not_modified: bool
    etag: str | None
    content: bytes | None
    digest: str | None


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds a ``Retry-After`` header asks for, clamped; ``None`` when absent or unusable.

    Accepts delta-seconds and HTTP-dates. Non-finite or unparsable values are
    treated as absent; everything else is clamped to ``[0, MAX_RETRY_AFTER_SECONDS]``.
    """
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


#: ``(scheme, host, port)``, with the scheme's default port filled in and the
#: host in its canonical form (see :func:`canonical_host`).
Origin = tuple[str, str, int]

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_host(raw_host: str) -> str | None:
    """The on-the-wire (ASCII) host ``raw_host`` lower-cased, without one trailing
    dot; ``None`` when it is empty or not ASCII.

    ``raw_host`` is ``httpx.URL(...).raw_host``: internationalised names are
    already in the punycode form the client connects to, so ``b.example.`` and
    ``b.example`` match, and so do ``bücher.example`` and ``xn--bcher-kva.example``.
    """
    if not raw_host.isascii():
        return None
    host = raw_host.lower()
    if host.endswith("."):
        host = host[:-1]
    return host or None


def url_origin(url: str) -> Origin | None:
    """The origin of an ``http(s)`` URL as the HTTP client sees it; ``None`` for
    anything else, or for a URL the client would refuse."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError, TypeError):
        return None
    scheme = parsed.scheme
    if scheme not in _DEFAULT_PORTS:
        return None
    port = parsed.port
    if port is not None and not 0 < port < 65536:
        return None
    try:
        host = canonical_host(parsed.raw_host.decode("ascii"))
    except UnicodeDecodeError:
        return None
    if host is None:
        return None
    return scheme, host, port or _DEFAULT_PORTS[scheme]


@dataclass(frozen=True)
class OriginCredentials:
    """A source's credential headers and the origins allowed to receive them.

    ``headers`` is awaited at most once per fetch, and only when a request is
    about to go to one of ``origins``.
    """

    origins: frozenset[Origin]
    headers: Callable[[], Awaitable[Mapping[str, str]]]


def _parsed_userinfo(url: str) -> bool:
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        return True
    try:
        return bool(httpx.URL(url).userinfo)
    except httpx.InvalidURL:
        return False


def has_userinfo(url: str) -> bool:
    """Whether ``url`` carries a ``user[:password]@`` part, by either URL parser.

    ``url`` is read as given and as the egress guard reads it
    (:func:`with_default_scheme`), so a scheme-less ``user:pass@host/x`` counts.
    httpx turns userinfo into its own ``Authorization: Basic`` header, so a URL
    carrying it is never sent: whoever wrote the URL would choose that header.
    """
    return _parsed_userinfo(url) or _parsed_userinfo(with_default_scheme(url))


def _shown(url: str) -> str:
    """``url`` for an error message: never one that carries userinfo."""
    return "a URL carrying credentials" if has_userinfo(url) else url


def _size_error(max_bytes: int) -> CatalogFetchError:
    return CatalogFetchError(f"response exceeds size limit ({max_bytes / (1024 * 1024):.0f} MB)")


async def _send_capped(
    client: httpx.AsyncClient, url: str, headers: Mapping[str, str], max_bytes: int
) -> tuple[httpx.Response, bytes]:
    """One GET; a 2xx body is streamed and refused once it passes ``max_bytes``."""
    response = await client.send(
        client.build_request("GET", url, headers=dict(headers)), stream=True
    )
    try:
        if not 200 <= response.status_code < 300:
            return response, b""
        length = response.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise _size_error(max_bytes)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise _size_error(max_bytes)
        return response, bytes(body)
    finally:
        await response.aclose()


async def _validated(url: str, config: IngestConfig) -> str:
    """The URL the egress guard returns for ``url``; refused if it carries userinfo.

    Userinfo is checked on the URL that would be sent, after the guard has
    normalised it, as well as on ``url`` itself. Never echo the URL: it
    carries a secret.
    """
    refused = CatalogFetchError("unsafe URL rejected: URL carries credentials (user:pass@)")
    if has_userinfo(url):
        raise refused
    try:
        validated = await validate_upstream_url_async(url, config.egress)
    except ValueError as exc:
        raise CatalogFetchError(f"unsafe URL rejected: {exc}") from exc
    if has_userinfo(validated):
        raise refused
    return validated


def _redirect_target(current: str, location: str) -> str:
    """``location`` resolved against ``current`` by httpx, the parser that sends it."""
    try:
        return str(httpx.URL(current).join(location))
    except (httpx.InvalidURL, ValueError, TypeError):
        # The location is upstream-controlled; it is not repeated.
        raise CatalogFetchError("redirect to an invalid URL refused") from None


async def _get(
    url: str,
    *,
    config: IngestConfig,
    client: httpx.AsyncClient | None,
    user_agent: str,
    conditional: Mapping[str, str],
    credentials: OriginCredentials | None,
) -> tuple[httpx.Response, bytes]:
    """GET ``url`` following redirects by hand, re-validating every hop.

    ``conditional`` headers (``If-None-Match``) go on every hop. ``credentials``
    go on a hop only when the origin of the URL actually sent there (the one
    the egress guard returned) is one of ``credentials.origins``, so a redirect
    to any other host never carries them. A hop whose URL carries userinfo is
    refused (see :func:`has_userinfo`).
    """
    validated_url = await _validated(url, config)
    resolved: Mapping[str, str] | None = None

    async def _headers_for(target: str) -> dict[str, str]:
        nonlocal resolved
        out = {"user-agent": user_agent, **conditional}
        if credentials is not None and url_origin(target) in credentials.origins:
            if resolved is None:
                resolved = await credentials.headers()
            out.update(resolved)
        return out

    async def _follow(http: httpx.AsyncClient) -> tuple[httpx.Response, bytes]:
        target = validated_url
        resp, body = await _send_capped(
            http, target, await _headers_for(target), config.max_spec_bytes
        )
        for _ in range(config.max_redirects):
            if resp.status_code < 300 or resp.status_code >= 400:
                return resp, body
            location = resp.headers.get("location")
            if not location:
                return resp, body
            target = await _validated(_redirect_target(target, location), config)
            resp, body = await _send_capped(
                http, target, await _headers_for(target), config.max_spec_bytes
            )
        if 300 <= resp.status_code < 400:
            raise CatalogFetchError("too many redirects")
        return resp, body

    try:
        if client is not None:
            return await _follow(client)
        async with httpx.AsyncClient(
            timeout=config.fetch_timeout_s,
            follow_redirects=False,
            transport=build_pinned_transport(config.egress),
        ) as own:
            return await _follow(own)
    except CatalogFetchError:
        raise
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise CatalogFetchError(f"failed to fetch {_shown(url)}: {exc}") from exc


def _raise_for_status(resp: httpx.Response, url: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    retry_after = (
        parse_retry_after(resp.headers.get("retry-after"))
        if resp.status_code in _RETRY_AFTER_STATUSES
        else None
    )
    raise CatalogFetchError(
        f"non-success status {resp.status_code} fetching {_shown(url)}",
        status_code=resp.status_code,
        retry_after_s=retry_after,
    )


async def fetch_json(
    url: str,
    *,
    config: IngestConfig,
    client: httpx.AsyncClient | None = None,
    user_agent: str = CATALOG_USER_AGENT,
    credentials: OriginCredentials | None = None,
) -> dict[str, Any]:
    """GET a URL and parse the body as JSON, with SSRF + redirect + size guards.

    Mirrors the hardening in ``registry.ingest.fetch.load_specification`` (manual
    redirect following with per-hop URL revalidation, content-length + body-size
    caps) but returns a plain decoded JSON object instead of an IngestSpecification.
    """
    resp, body = await _get(
        url,
        config=config,
        client=client,
        user_agent=user_agent,
        conditional={},
        credentials=credentials,
    )
    _raise_for_status(resp, url)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CatalogFetchError(f"failed to parse JSON from {_shown(url)}") from exc
    if not isinstance(parsed, dict):
        raise CatalogFetchError("expected a JSON object")
    return parsed


async def fetch_bytes_conditional(
    url: str,
    *,
    config: IngestConfig,
    etag: str | None = None,
    client: httpx.AsyncClient | None = None,
    user_agent: str = CATALOG_USER_AGENT,
    credentials: OriginCredentials | None = None,
) -> ConditionalFetch:
    """Conditionally GET a URL's raw bytes, with the same SSRF/redirect/size guards.

    When ``etag`` is provided it is sent as ``If-None-Match`` (on every redirect
    hop) so an unchanged resource answers ``304 Not Modified`` with no body
    transferred (``ConditionalFetch.not_modified`` is then ``True``). Otherwise the
    body is returned with its ``sha256`` digest and the response ``ETag``.

    raw.githubusercontent.com serves content-derived ETags, so a stable ETag (or
    stable digest) means the upstream spec is byte-identical to what we last saw.
    """
    conditional = {"If-None-Match": etag} if etag else {}
    resp, body = await _get(
        url,
        config=config,
        client=client,
        user_agent=user_agent,
        conditional=conditional,
        credentials=credentials,
    )
    if resp.status_code == 304:
        return ConditionalFetch(
            not_modified=True, etag=resp.headers.get("etag") or etag, content=None, digest=None
        )
    _raise_for_status(resp, url)
    return ConditionalFetch(
        not_modified=False,
        etag=resp.headers.get("etag"),
        content=body,
        digest=hashlib.sha256(body).hexdigest(),
    )
