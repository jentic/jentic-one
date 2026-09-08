"""Tests for URL-based spec loading via load_specification."""

import hashlib
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from jentic_one.registry.ingest.exc import IngestStageError
from jentic_one.registry.ingest.fetch import UrlSource, load_specification
from jentic_one.shared.config import EgressConfig, IngestConfig

_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Remote API", "version": "2.0.0", "x-vendor": "remote-co"},
}
_SPEC_BYTES = json.dumps(_SPEC).encode()


def _url_source(url: str = "https://example.com/specs/openapi.json", **kwargs: Any) -> UrlSource:
    return UrlSource(type="url", url=url, **kwargs)


def _mock_client(
    body: bytes = _SPEC_BYTES,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            content=body,
            headers=headers or {"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_url_fetch_produces_valid_specification() -> None:
    client = _mock_client()
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        result = await load_specification(_url_source())

    assert result.spec_type == "openapi"
    assert result.api_identifier.vendor == "remote-co"
    assert result.api_identifier.name == "remote-api"
    assert result.api_identifier.version == "2.0.0"
    assert result.sha == hashlib.sha256(_SPEC_BYTES).hexdigest()
    assert result.source_type == "url"
    assert result.source_url == "https://example.com/specs/openapi.json"


@pytest.mark.asyncio
async def test_non_200_raises_ingest_stage_error() -> None:
    client = _mock_client(status_code=404, body=b"not found")
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(IngestStageError, match="non-success status 404"):
            await load_specification(_url_source())


@pytest.mark.asyncio
async def test_oversized_content_length_raises() -> None:
    client = _mock_client(
        body=b"small",
        headers={"content-length": "40000000", "content-type": "application/json"},
    )
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(IngestStageError, match=r"25\.0 MB"):
            await load_specification(_url_source())


@pytest.mark.asyncio
async def test_oversized_body_raises() -> None:
    big_body = b"x" * (26 * 1024 * 1024)
    client = _mock_client(body=big_body)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(IngestStageError, match=r"25\.0 MB"):
            await load_specification(_url_source())


@pytest.mark.asyncio
async def test_network_error_raises_ingest_stage_error() -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(error_handler)
    client = httpx.AsyncClient(transport=transport)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(IngestStageError, match="failed to fetch URL"):
            await load_specification(_url_source())


@pytest.mark.asyncio
async def test_private_ip_url_rejected_as_ssrf() -> None:
    with pytest.raises(IngestStageError, match="unsafe URL rejected"):
        await load_specification(_url_source(url="http://169.254.169.254/latest/meta-data/"))


@pytest.mark.asyncio
async def test_loopback_url_rejected_as_ssrf() -> None:
    with pytest.raises(IngestStageError, match="unsafe URL rejected"):
        await load_specification(_url_source(url="http://127.0.0.1:5432/"))


@pytest.mark.asyncio
async def test_redirect_to_private_ip_rejected_as_ssrf() -> None:
    def redirect_handler(request: httpx.Request) -> httpx.Response:
        if "example.com" in str(request.url):
            return httpx.Response(
                status_code=302,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
            )
        return httpx.Response(status_code=200, content=_SPEC_BYTES)

    transport = httpx.MockTransport(redirect_handler)
    client = httpx.AsyncClient(transport=transport)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(IngestStageError, match="unsafe URL rejected"):
            await load_specification(_url_source())


@pytest.mark.asyncio
async def test_valid_redirect_followed_successfully() -> None:
    call_count = 0

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                status_code=301,
                headers={"location": "https://cdn.example.com/specs/openapi.json"},
            )
        return httpx.Response(
            status_code=200,
            content=_SPEC_BYTES,
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(redirect_handler)
    client = httpx.AsyncClient(transport=transport)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        result = await load_specification(_url_source())

    assert result.spec_type == "openapi"
    assert call_count == 2


@pytest.mark.asyncio
async def test_relative_redirect_resolved_via_urljoin() -> None:
    call_count = 0

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                status_code=302,
                headers={"location": "/other/openapi.json"},
            )
        return httpx.Response(
            status_code=200,
            content=_SPEC_BYTES,
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(redirect_handler)
    client = httpx.AsyncClient(transport=transport)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        result = await load_specification(_url_source())

    assert result.spec_type == "openapi"
    assert call_count == 2


@pytest.mark.asyncio
async def test_too_many_redirects_raises() -> None:
    cfg = IngestConfig(max_redirects=2)

    def always_redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=302,
            headers={"location": "https://example.com/next"},
        )

    transport = httpx.MockTransport(always_redirect)
    client = httpx.AsyncClient(transport=transport)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(IngestStageError, match="too many redirects"):
            await load_specification(_url_source(), config=cfg)


@pytest.mark.asyncio
async def test_malformed_content_length_falls_through_to_body_check() -> None:
    client = _mock_client(
        body=_SPEC_BYTES,
        headers={"content-length": "abc", "content-type": "application/json"},
    )
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = client
        result = await load_specification(_url_source())

    assert result.spec_type == "openapi"


_INTERNAL_HOST = "jentic-smoke-upstream.default.svc.cluster.local"
_CLUSTER_IP = "10.96.12.34"
_INTERNAL_URL = f"http://{_INTERNAL_HOST}:8084/specs/live.json"

_CLUSTER_EGRESS = EgressConfig(
    allowed_private_subnets=["10.96.0.0/12"],
    allowed_internal_domains=[".svc.cluster.local"],
)


def _resolve_to(ip: str) -> Any:
    """Patch DNS resolution so an internal hostname maps to a fixed IP."""

    def _getaddrinfo(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [(2, 1, 6, "", (ip, 0))]

    return patch("jentic_one.shared.url_validation.socket.getaddrinfo", _getaddrinfo)


@pytest.mark.asyncio
async def test_internal_domain_rejected_under_default_egress() -> None:
    """An in-cluster host resolving to a private IP is blocked without an allowlist."""
    with _resolve_to(_CLUSTER_IP), pytest.raises(IngestStageError, match="unsafe URL rejected"):
        await load_specification(_url_source(url=_INTERNAL_URL))


@pytest.mark.asyncio
async def test_internal_domain_allowed_with_egress_allowlist() -> None:
    """With a matching egress allowlist the in-cluster spec fetch is permitted."""
    client = _mock_client()
    cfg = IngestConfig(egress=_CLUSTER_EGRESS)
    with (
        _resolve_to(_CLUSTER_IP),
        patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient") as mock_cls,
    ):
        mock_cls.return_value = client
        result = await load_specification(_url_source(url=_INTERNAL_URL), config=cfg)

    assert result.spec_type == "openapi"
    assert result.source_url == _INTERNAL_URL


@pytest.mark.asyncio
async def test_metadata_ip_rejected_even_with_egress_allowlist() -> None:
    """The cloud-metadata IP is a hard deny that no allowlist can open."""
    cfg = IngestConfig(
        egress=EgressConfig(
            allowed_private_subnets=["169.254.0.0/16"],
            allowed_internal_domains=[".svc.cluster.local"],
        )
    )
    with pytest.raises(IngestStageError, match="unsafe URL rejected"):
        await load_specification(
            _url_source(url="http://169.254.169.254/latest/meta-data/"), config=cfg
        )
