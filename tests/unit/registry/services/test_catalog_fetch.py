"""Tests for the catalog slice's SSRF-guarded JSON fetch helper (``fetch_json``).

Focuses on the response size guard, which gates catalog manifest + spec
previews. A regression here (the cap being too small) made large public-API
specs un-previewable with ``catalog spec unavailable: response exceeds size
limit``; these tests pin the configured limit and the labelled error message.
"""

import hashlib
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from jentic_one.registry.services.catalog.fetch import (
    CatalogFetchError,
    fetch_bytes_conditional,
    fetch_json,
)
from jentic_one.shared.config import IngestConfig
from jentic_one.shared.egress import DnsPinningTransport

_SPEC: dict[str, Any] = {"openapi": "3.1.0", "info": {"title": "Big API", "version": "1.0.0"}}
_SPEC_BYTES = json.dumps(_SPEC).encode()


def _mock_client(
    body: bytes = _SPEC_BYTES,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            content=body,
            headers=headers or {"content-type": "application/json"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _patched(client: httpx.AsyncClient) -> Any:
    return patch(
        "jentic_one.registry.services.catalog.fetch.httpx.AsyncClient",
        return_value=client,
    )


@pytest.mark.asyncio
async def test_default_cap_is_25_mib() -> None:
    """A 20 MiB spec (was over the old 5 MiB cap) now fetches successfully."""
    big = b'{"openapi":"3.1.0","info":{"title":"x","version":"1"},"_pad":"'
    big += b"a" * (20 * 1024 * 1024) + b'"}'
    client = _mock_client(body=big)
    with _patched(client):
        result = await fetch_json("https://example.com/openapi.json", config=IngestConfig())
    assert result["openapi"] == "3.1.0"


@pytest.mark.asyncio
async def test_oversized_body_raises_with_limit_label() -> None:
    big_body = b"x" * (26 * 1024 * 1024)
    client = _mock_client(body=big_body)
    with _patched(client), pytest.raises(CatalogFetchError, match=r"size limit \(25 MB\)"):
        await fetch_json("https://example.com/openapi.json", config=IngestConfig())


@pytest.mark.asyncio
async def test_oversized_content_length_raises_with_limit_label() -> None:
    client = _mock_client(
        body=b"small",
        headers={"content-length": "40000000", "content-type": "application/json"},
    )
    with _patched(client), pytest.raises(CatalogFetchError, match=r"size limit \(25 MB\)"):
        await fetch_json("https://example.com/openapi.json", config=IngestConfig())


@pytest.mark.asyncio
async def test_custom_cap_is_respected() -> None:
    """The cap tracks IngestConfig.max_spec_bytes, so a tuned-down deploy still guards."""
    client = _mock_client(body=b"x" * 2048)
    with _patched(client), pytest.raises(CatalogFetchError, match="size limit"):
        await fetch_json(
            "https://example.com/openapi.json",
            config=IngestConfig(max_spec_bytes=1024),
        )


# ── fetch_bytes_conditional (update-notify probe) ───────────────────────────


def _mock_conditional_client(
    *, body: bytes = _SPEC_BYTES, etag: str | None = '"v1"'
) -> httpx.AsyncClient:
    """A transport that honours ``If-None-Match``: matching etag → 304, else 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        inm = request.headers.get("if-none-match")
        if etag is not None and inm == etag:
            return httpx.Response(status_code=304, headers={"etag": etag})
        headers = {"content-type": "application/json"}
        if etag is not None:
            headers["etag"] = etag
        return httpx.Response(status_code=200, content=body, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_conditional_first_fetch_returns_bytes_etag_and_digest() -> None:
    """No prior etag → full body, its sha256 digest, and the response etag."""
    client = _mock_conditional_client()
    with _patched(client):
        result = await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig()
        )
    assert result.not_modified is False
    assert result.content == _SPEC_BYTES
    assert result.digest == hashlib.sha256(_SPEC_BYTES).hexdigest()
    assert result.etag == '"v1"'


@pytest.mark.asyncio
async def test_conditional_matching_etag_yields_not_modified() -> None:
    """A stored etag matching upstream → 304, no body/digest transferred."""
    client = _mock_conditional_client()
    with _patched(client):
        result = await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig(), etag='"v1"'
        )
    assert result.not_modified is True
    assert result.content is None
    assert result.digest is None
    assert result.etag == '"v1"'


@pytest.mark.asyncio
async def test_conditional_stale_etag_returns_new_bytes() -> None:
    """A stored etag that no longer matches → fresh 200 with new body + etag."""
    client = _mock_conditional_client(etag='"v2"')
    with _patched(client):
        result = await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig(), etag='"v1"'
        )
    assert result.not_modified is False
    assert result.content == _SPEC_BYTES
    assert result.etag == '"v2"'


@pytest.mark.asyncio
async def test_conditional_no_etag_from_upstream_still_returns_digest() -> None:
    """When upstream sends no ETag, the digest still drives change detection."""
    client = _mock_conditional_client(etag=None)
    with _patched(client):
        result = await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig()
        )
    assert result.not_modified is False
    assert result.etag is None
    assert result.digest is not None


@pytest.mark.asyncio
async def test_conditional_oversized_body_raises() -> None:
    client = _mock_conditional_client(body=b"x" * (26 * 1024 * 1024), etag=None)
    with _patched(client), pytest.raises(CatalogFetchError, match="size limit"):
        await fetch_bytes_conditional("https://example.com/openapi.json", config=IngestConfig())


@pytest.mark.asyncio
async def test_conditional_rejects_unsafe_url() -> None:
    with pytest.raises(CatalogFetchError, match="unsafe URL"):
        await fetch_bytes_conditional("http://169.254.169.254/latest", config=IngestConfig())


@pytest.mark.asyncio
async def test_conditional_oversized_content_length_raises() -> None:
    """The pre-body content-length guard also gates the conditional path."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"small",
            headers={"content-length": "40000000", "content-type": "application/json"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with _patched(client), pytest.raises(CatalogFetchError, match=r"size limit \(25 MB\)"):
        await fetch_bytes_conditional("https://example.com/openapi.json", config=IngestConfig())


@pytest.mark.asyncio
async def test_conditional_revalidates_redirect_target_against_ssrf() -> None:
    """A redirect to a private/link-local address is rejected mid-hop (SSRF-via-redirect).

    This pins the per-hop ``validate_upstream_url`` in the redirect loop: dropping
    it would turn an innocuous first URL into an SSRF against cloud metadata.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(
                status_code=302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        # Should never be reached — the guard must reject the redirect target first.
        return httpx.Response(status_code=200, content=b"{}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with _patched(client), pytest.raises(CatalogFetchError, match="unsafe URL"):
        await fetch_bytes_conditional("https://example.com/openapi.json", config=IngestConfig())


@pytest.mark.asyncio
async def test_conditional_resends_if_none_match_across_redirects() -> None:
    """The conditional header is re-sent on the redirected hop, so a 304 still works."""
    seen_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("if-none-match"))
        if request.url.path == "/openapi.json":
            return httpx.Response(
                status_code=302, headers={"location": "https://example.com/moved.json"}
            )
        # Redirected hop: honour If-None-Match → 304.
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(status_code=304, headers={"etag": '"v1"'})
        return httpx.Response(status_code=200, content=_SPEC_BYTES, headers={"etag": '"v1"'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with _patched(client):
        result = await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig(), etag='"v1"'
        )
    assert result.not_modified is True
    # Both the initial request and the redirected hop carried the conditional header.
    assert seen_headers == ['"v1"', '"v1"']


@pytest.mark.asyncio
async def test_conditional_too_many_redirects_raises() -> None:
    """A redirect chain longer than the budget is refused, not followed forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect to a safe-but-different URL to exhaust the budget.
        nxt = f"https://example.com/hop-{request.url.path}"
        return httpx.Response(status_code=302, headers={"location": nxt})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with _patched(client), pytest.raises(CatalogFetchError, match="too many redirects"):
        await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig(max_redirects=2)
        )


@pytest.mark.asyncio
async def test_conditional_sends_user_agent() -> None:
    """The poller identifies itself with a descriptive User-Agent (politeness)."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(status_code=200, content=b"{}", headers={"etag": '"v1"'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with _patched(client):
        await fetch_bytes_conditional("https://example.com/openapi.json", config=IngestConfig())
    assert seen and seen[0] is not None
    assert "jentic-one-catalog" in seen[0]


@pytest.mark.asyncio
async def test_conditional_304_propagates_refreshed_etag() -> None:
    """A 304 that returns a *new* validator surfaces it so the next probe re-sends it."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=304, headers={"etag": '"v1-refreshed"'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with _patched(client):
        result = await fetch_bytes_conditional(
            "https://example.com/openapi.json", config=IngestConfig(), etag='"v1"'
        )
    assert result.not_modified is True
    assert result.etag == '"v1-refreshed"'


@pytest.mark.asyncio
async def test_fetch_wires_dns_pinning_transport_by_default() -> None:
    """L5: the catalog fetchers pin DNS at connect time, closing the rebind window.

    ``validate_upstream_url`` resolves+checks before the request, but httpx re-resolves
    at connect time — a rebind window. The fetchers must build the client with a
    :class:`DnsPinningTransport` so the connection is pinned to a validated IP. Assert
    on the ``transport`` kwarg passed to ``httpx.AsyncClient`` (the constructor is
    mocked in these tests, so the transport isn't otherwise exercised here).
    """
    captured: dict[str, Any] = {}
    stub = _mock_conditional_client()  # built before patching to avoid recursion

    def _capturing_client(*_args: Any, **kwargs: Any) -> httpx.AsyncClient:
        captured["transport"] = kwargs.get("transport")
        return stub

    with patch(
        "jentic_one.registry.services.catalog.fetch.httpx.AsyncClient",
        side_effect=_capturing_client,
    ):
        await fetch_bytes_conditional("https://example.com/openapi.json", config=IngestConfig())
    assert isinstance(captured["transport"], DnsPinningTransport)


@pytest.mark.asyncio
async def test_fetch_no_pinning_transport_when_disabled() -> None:
    """With pinning disabled the client uses httpx's default transport (None)."""
    captured: dict[str, Any] = {}
    stub = _mock_conditional_client()  # built before patching to avoid recursion

    def _capturing_client(*_args: Any, **kwargs: Any) -> httpx.AsyncClient:
        captured["transport"] = kwargs.get("transport")
        return stub

    cfg = IngestConfig()
    cfg.egress.dns_pinning_enabled = False
    with patch(
        "jentic_one.registry.services.catalog.fetch.httpx.AsyncClient",
        side_effect=_capturing_client,
    ):
        await fetch_bytes_conditional("https://example.com/openapi.json", config=cfg)
    assert captured["transport"] is None
