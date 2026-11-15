"""Broker URL validation and reverse-proxy path-prefix preservation for ``/mcp`` execute."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

import jentic_one.mcp.execute as ex
import jentic_one.mcp.tools as tools_mod
from jentic_one.mcp.envelopes import CODE_RESOLVE_FAILED, CODE_TRANSPORT_ERROR, ToolError
from jentic_one.mcp.execute import broker_request_url, resolve_broker_target
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType


def _make_env(broker_url: str) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    server = ServerConfig()
    server.mcp.enabled = True
    server.mcp.broker_url = broker_url
    ctx.config.server = server
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub="agnt_1", permissions=["apis:read"], actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


@pytest.mark.parametrize(
    "broker_url",
    [
        "",
        "127.0.0.1:8100",
        "https://",
        "ws://localhost:8100",
        "wss://gateway.internal/broker",
        "ftp://127.0.0.1:8100",
        "file:///tmp/broker.sock",
        "capacitor://localhost",
        "https://user@gateway.internal",
        "https://user:pass@gateway.internal",  # pragma: allowlist secret
        "https://gateway.internal/broker?foo=1",
        "https://gateway.internal/broker#frag",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:abc",
        "https://gateway.internal//broker",
        "https://gateway.internal/../admin",
        "https://gateway.internal/./broker",
    ],
)
def test_resolve_broker_target_rejects_non_http_or_malformed_urls(broker_url: str) -> None:
    """Non-HTTP(S) schemes and malformed URL components fail with ``CODE_RESOLVE_FAILED``."""
    with pytest.raises(ToolError) as exc_info:
        resolve_broker_target(broker_url)
    assert exc_info.value.code == CODE_RESOLVE_FAILED


def test_resolve_broker_target_rejects_plaintext_non_loopback() -> None:
    """SEC-1 remains enforced for plaintext ``http`` against non-loopback hosts."""
    with pytest.raises(ToolError) as exc_info:
        resolve_broker_target("http://gateway.internal/broker")
    assert exc_info.value.code == CODE_TRANSPORT_ERROR


@pytest.mark.parametrize(
    ("broker_url", "want_scheme", "want_target"),
    [
        ("http://127.0.0.1:8100", "http", "127.0.0.1:8100"),
        ("http://127.0.0.1:8100/", "http", "127.0.0.1:8100"),
        ("HTTP://LOCALHOST:8100/broker/", "http", "LOCALHOST:8100/broker"),
        ("https://gateway.internal/broker", "https", "gateway.internal/broker"),
        ("https://gateway.internal/broker/", "https", "gateway.internal/broker"),
        (
            "https://gateway.internal:8443/api/v1/broker/",
            "https",
            "gateway.internal:8443/api/v1/broker",
        ),
    ],
)
def test_resolve_broker_target_preserves_normalized_path_prefix(
    broker_url: str, want_scheme: str, want_target: str
) -> None:
    assert resolve_broker_target(broker_url) == (want_scheme, want_target)


@pytest.mark.parametrize(
    ("broker_url", "upstream", "broker_relative", "want_url"),
    [
        (
            "https://gateway.internal/broker",
            "/v1/pets?limit=5",
            True,
            "https://gateway.internal/broker/v1/pets?limit=5",
        ),
        (
            "https://gateway.internal/broker/",
            "/v1/pets",
            True,
            "https://gateway.internal/broker/v1/pets",
        ),
        (
            "https://gateway.internal/broker",
            "https://api.example.com/v1/pets?limit=5",
            False,
            "https://gateway.internal/broker/https://api.example.com/v1/pets?limit=5",
        ),
        (
            "https://gateway.internal/broker/",
            "https://api.example.com/v1/pets",
            False,
            "https://gateway.internal/broker/https://api.example.com/v1/pets",
        ),
        (
            "http://127.0.0.1:8100/",
            "/v1/pets",
            True,
            "http://127.0.0.1:8100/v1/pets",
        ),
        (
            "http://127.0.0.1:8100/",
            "https://api.example.com/v1/pets",
            False,
            "http://127.0.0.1:8100/https://api.example.com/v1/pets",
        ),
    ],
)
def test_broker_request_url_joins_prefix_without_double_slashes(
    broker_url: str, upstream: str, broker_relative: bool, want_url: str
) -> None:
    scheme, host = resolve_broker_target(broker_url)
    assert broker_request_url(scheme, host, upstream, broker_relative=broker_relative) == want_url


def test_broker_request_url_accepts_explicit_base_path_kwarg() -> None:
    assert (
        broker_request_url(
            "https",
            "gateway.internal/",
            "/v1/pets",
            broker_relative=True,
            base_path="/broker/",
        )
        == "https://gateway.internal/broker/v1/pets"
    )


def _decode_tool_json(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


async def test_execute_with_ws_broker_url_returns_coded_resolve_failed() -> None:
    """A ``ws://localhost:8100`` broker URL fails closed at target resolution
    rather than reaching ``httpx`` and raising ``UnsupportedProtocol``."""
    env = _make_env("ws://localhost:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert result.is_error
    payload = _decode_tool_json(result)
    assert payload["error_code"] == CODE_RESOLVE_FAILED
    assert "scheme" in payload["error"]


async def test_execute_forwards_through_broker_path_prefix_for_both_target_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ``METHOD:/path`` and inspected absolute-URL operations preserve a
    reverse-proxy path prefix on ``server.mcp.broker_url`` without double slashes."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{}")

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    async def fake_inspect(env: CallEnv, target: str, revision: str) -> dict[str, Any]:
        return {"method": "GET", "url": "https://upstream.example/v1/pets"}

    monkeypatch.setattr(ex, "_broker_client", client_factory)
    monkeypatch.setattr(tools_mod, "_inspect_document", fake_inspect)

    env = _make_env("https://gateway.internal/broker/")

    rel_res = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert not rel_res.is_error, rel_res.content

    abs_res = await dispatch_tool_call(env, "execute", {"operation_id": "listPets"})
    assert not abs_res.is_error, abs_res.content

    assert seen_urls == [
        "https://gateway.internal/broker/v1/pets",
        "https://gateway.internal/broker/https://upstream.example/v1/pets",
    ]
