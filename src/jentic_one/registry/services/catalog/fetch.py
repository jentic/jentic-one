"""HTTP fetch helper for the catalog slice — SSRF-guarded GET of JSON documents.

A thin, redirect-safe wrapper around ``httpx.AsyncClient`` that reuses the same
``validate_upstream_url_async`` SSRF guard, ingest-config knobs (timeout, size cap,
redirect budget) and streamed byte-capped body read (``shared.bounded_read``) as
the import fetch layer. It exists so the catalog service can
pull the upstream manifest + individual specs without duplicating the redirect/size
hardening and without coupling to the ingest pipeline's spec-shaped return type.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from jentic_one.shared.bounded_read import (
    ResponseTooLargeError,
    UndecodableBodyError,
    decode_body,
    follow_redirects,
    read_capped_body,
)
from jentic_one.shared.config import IngestConfig
from jentic_one.shared.egress import build_pinned_transport
from jentic_one.shared.url_validation import validate_upstream_url_async


class CatalogFetchError(Exception):
    """Raised when an upstream catalog document cannot be fetched or parsed."""


#: Identify the catalog fetcher/update-notify poller to upstream hosts. Server-
#: initiated recurring third-party fetches should announce themselves so operators
#: can attribute traffic and hosts can apply sane rate limits (unidentified bots
#: are throttled first). See the raw.githubusercontent poll path in the catalog
#: update-notify sweep.
_USER_AGENT = "jentic-one-catalog/1 (+https://github.com/jentic/jentic-one)"


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


async def fetch_json(url: str, *, config: IngestConfig) -> dict[str, Any]:
    """GET a URL and parse the body as JSON, with SSRF + redirect + size guards.

    Mirrors the hardening in ``registry.ingest.fetch.load_specification`` (manual
    redirect following with per-hop URL revalidation, content-length + streamed
    body-size caps) but returns a plain decoded JSON object instead of an
    IngestSpecification.
    """
    try:
        validated_url = await validate_upstream_url_async(url, config.egress)
    except ValueError as exc:
        raise CatalogFetchError(f"unsafe URL rejected: {exc}") from exc

    headers = {"user-agent": _USER_AGENT}
    max_bytes = config.max_spec_bytes
    limit_mb = max_bytes / (1024 * 1024)
    try:
        async with httpx.AsyncClient(
            timeout=config.fetch_timeout_s,
            follow_redirects=False,
            transport=build_pinned_transport(config.egress),
        ) as client:
            resp = await _open_following_redirects(
                client, validated_url, headers=headers, config=config
            )
            try:
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise CatalogFetchError(f"non-success status {resp.status_code} fetching {url}")
                body = await read_capped_body(resp, max_bytes)
            finally:
                await resp.aclose()
    except CatalogFetchError:
        raise
    except ResponseTooLargeError as exc:
        raise CatalogFetchError(f"response exceeds size limit ({limit_mb:.0f} MB)") from exc
    except UndecodableBodyError as exc:
        raise CatalogFetchError(f"failed to fetch {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CatalogFetchError(f"failed to fetch {url}: {exc}") from exc

    try:
        parsed = json.loads(decode_body(resp, body))
    except (json.JSONDecodeError, ValueError) as exc:
        raise CatalogFetchError(f"failed to parse JSON from {url}") from exc

    if not isinstance(parsed, dict):
        raise CatalogFetchError("expected a JSON object")
    return parsed


async def fetch_bytes_conditional(
    url: str, *, config: IngestConfig, etag: str | None = None
) -> ConditionalFetch:
    """Conditionally GET a URL's raw bytes, with the same SSRF/redirect/size guards.

    A cheap upstream-change probe for the update-notify sweep: when ``etag`` is
    provided it is sent as ``If-None-Match`` so an unchanged resource answers
    ``304 Not Modified`` with no body transferred (``ConditionalFetch.not_modified``
    is then ``True``). Otherwise the body is returned with its ``sha256`` digest
    and the response ``ETag``. Redirects are followed manually with per-hop URL
    revalidation and the same content-length + streamed body-size caps as
    ``fetch_json``; the conditional header is re-sent on each hop.

    raw.githubusercontent.com serves content-derived ETags, so a stable ETag (or
    stable digest) means the upstream spec is byte-identical to what we last saw.
    """
    try:
        validated_url = await validate_upstream_url_async(url, config.egress)
    except ValueError as exc:
        raise CatalogFetchError(f"unsafe URL rejected: {exc}") from exc

    headers = {"user-agent": _USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    max_bytes = config.max_spec_bytes
    limit_mb = max_bytes / (1024 * 1024)
    try:
        async with httpx.AsyncClient(
            timeout=config.fetch_timeout_s,
            follow_redirects=False,
            transport=build_pinned_transport(config.egress),
        ) as client:
            resp = await _open_following_redirects(
                client, validated_url, headers=headers, config=config
            )
            try:
                if resp.status_code == 304:
                    return ConditionalFetch(
                        not_modified=True,
                        etag=resp.headers.get("etag") or etag,
                        content=None,
                        digest=None,
                    )
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise CatalogFetchError(f"non-success status {resp.status_code} fetching {url}")
                body = await read_capped_body(resp, max_bytes)
            finally:
                await resp.aclose()
    except CatalogFetchError:
        raise
    except ResponseTooLargeError as exc:
        raise CatalogFetchError(f"response exceeds size limit ({limit_mb:.0f} MB)") from exc
    except UndecodableBodyError as exc:
        raise CatalogFetchError(f"failed to fetch {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CatalogFetchError(f"failed to fetch {url}: {exc}") from exc

    return ConditionalFetch(
        not_modified=False,
        etag=resp.headers.get("etag"),
        content=body,
        digest=hashlib.sha256(body).hexdigest(),
    )


async def _open_following_redirects(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str], config: IngestConfig
) -> httpx.Response:
    """Open ``url`` via ``shared.bounded_read.follow_redirects`` with catalog errors."""
    resp, _ = await follow_redirects(
        client,
        url,
        headers=headers,
        egress=config.egress,
        max_redirects=config.max_redirects,
        on_unsafe=lambda exc: CatalogFetchError(f"unsafe URL rejected: {exc}"),
        on_too_many=lambda: CatalogFetchError("too many redirects"),
    )
    return resp
