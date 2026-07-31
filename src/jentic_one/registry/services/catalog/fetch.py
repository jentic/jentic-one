"""HTTP fetch helper for the catalog slice — SSRF-guarded GET of JSON documents.

A thin, redirect-safe wrapper around ``httpx.AsyncClient`` that reuses the same
``validate_upstream_url`` SSRF guard and ingest-config knobs (timeout, size cap,
redirect budget) as the import fetch layer. It exists so the catalog service can
pull the upstream manifest + individual specs without duplicating the redirect/size
hardening and without coupling to the ingest pipeline's spec-shaped return type.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from jentic_one.shared.config import IngestConfig
from jentic_one.shared.url_validation import validate_upstream_url


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
    redirect following with per-hop URL revalidation, content-length + body-size
    caps) but returns a plain decoded JSON object instead of an IngestSpecification.
    """
    try:
        validated_url = validate_upstream_url(url, config.egress)
    except ValueError as exc:
        raise CatalogFetchError(f"unsafe URL rejected: {exc}") from exc

    max_bytes = config.max_spec_bytes
    try:
        async with httpx.AsyncClient(
            timeout=config.fetch_timeout_s,
            follow_redirects=False,
        ) as client:
            resp = await client.get(validated_url, headers={"user-agent": _USER_AGENT})
            for _ in range(config.max_redirects):
                if resp.status_code < 300 or resp.status_code >= 400:
                    break
                location = resp.headers.get("location")
                if not location:
                    break
                try:
                    validated_url = validate_upstream_url(
                        urljoin(validated_url, location), config.egress
                    )
                except ValueError as exc:
                    raise CatalogFetchError(f"unsafe URL rejected: {exc}") from exc
                resp = await client.get(validated_url, headers={"user-agent": _USER_AGENT})
            else:
                if 300 <= resp.status_code < 400:
                    raise CatalogFetchError("too many redirects")
    except CatalogFetchError:
        raise
    except httpx.HTTPError as exc:
        raise CatalogFetchError(f"failed to fetch {url}: {exc}") from exc

    if resp.status_code < 200 or resp.status_code >= 300:
        raise CatalogFetchError(f"non-success status {resp.status_code} fetching {url}")

    content_length = resp.headers.get("content-length")
    limit_mb = max_bytes / (1024 * 1024)
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise CatalogFetchError(f"response exceeds size limit ({limit_mb:.0f} MB)")
    if len(resp.content) > max_bytes:
        raise CatalogFetchError(f"response exceeds size limit ({limit_mb:.0f} MB)")

    try:
        parsed = json.loads(resp.text)
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
    revalidation and the same content-length + body-size caps as ``fetch_json``;
    the conditional header is re-sent on each hop.

    raw.githubusercontent.com serves content-derived ETags, so a stable ETag (or
    stable digest) means the upstream spec is byte-identical to what we last saw.
    """
    try:
        validated_url = validate_upstream_url(url, config.egress)
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
        ) as client:
            resp = await client.get(validated_url, headers=headers)
            for _ in range(config.max_redirects):
                if resp.status_code < 300 or resp.status_code >= 400:
                    break
                location = resp.headers.get("location")
                if not location:
                    break
                try:
                    validated_url = validate_upstream_url(
                        urljoin(validated_url, location), config.egress
                    )
                except ValueError as exc:
                    raise CatalogFetchError(f"unsafe URL rejected: {exc}") from exc
                resp = await client.get(validated_url, headers=headers)
            else:
                if 300 <= resp.status_code < 400:
                    raise CatalogFetchError("too many redirects")
    except CatalogFetchError:
        raise
    except httpx.HTTPError as exc:
        raise CatalogFetchError(f"failed to fetch {url}: {exc}") from exc

    if resp.status_code == 304:
        return ConditionalFetch(
            not_modified=True, etag=resp.headers.get("etag") or etag, content=None, digest=None
        )

    if resp.status_code < 200 or resp.status_code >= 300:
        raise CatalogFetchError(f"non-success status {resp.status_code} fetching {url}")

    content_length = resp.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise CatalogFetchError(f"response exceeds size limit ({limit_mb:.0f} MB)")

    body = resp.content
    if len(body) > max_bytes:
        raise CatalogFetchError(f"response exceeds size limit ({limit_mb:.0f} MB)")

    return ConditionalFetch(
        not_modified=False,
        etag=resp.headers.get("etag"),
        content=body,
        digest=hashlib.sha256(body).hexdigest(),
    )
