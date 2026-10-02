"""SSRF guard pins for the vendor-facing calls in the connect flow.

Every raw ``httpx`` call in the connect flow (device authorization, token
exchange, identity echo) is fronted by ``validate_upstream_url`` so that a
mis-configured or tampered upstream cannot aim a platform-issued request at a
private / loopback / cloud-metadata target. These tests pin the guard at each
call site: an unsafe URL must raise the site's typed error BEFORE any HTTP is
opened. Each site also connects through the strict DNS-pinning transport, so a
host that re-resolves to a blocked address — or does not resolve at all — at
connect time is refused with the same typed error.
"""

from __future__ import annotations

import socket
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

import pytest

from jentic_one.control.services.credentials.providers.oauth2 import (
    OAuth2Provider,
    TokenExchangeError,
)
from jentic_one.control.services.integrations.device_authorization import (
    DeviceAuthorizationUpstreamError,
    begin_device_authorization,
    poll_device_authorization,
)
from jentic_one.control.services.integrations.identity_echo import (
    IdentityEchoError,
    echo_identity,
)
from jentic_one.shared.config import VendorIdentityProbeConfig

# Loopback address — the strict-default egress policy rejects the whole
# 127.0.0.0/8 range and there is no operator opt-in on the control-plane
# call sites (they pass ``egress=None``). Any of the blocked ranges works;
# loopback is the most obvious "should never legitimately be a vendor".
_UNSAFE_URL = "http://127.0.0.1/x"


@pytest.mark.asyncio()
async def test_begin_device_authorization_refuses_unsafe_url() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(DeviceAuthorizationUpstreamError) as exc_info:
            await begin_device_authorization(
                authorization_endpoint=_UNSAFE_URL,
                client_id="cid",
                scopes=[],
            )
        assert exc_info.value.status == 0
        # The guard must fire BEFORE any HTTP client is constructed —
        # a compromised URL never gets a chance to receive a request.
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio()
async def test_poll_device_authorization_refuses_unsafe_url() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(DeviceAuthorizationUpstreamError) as exc_info:
            await poll_device_authorization(
                token_endpoint=_UNSAFE_URL,
                client_id="cid",
                device_code="dc",
            )
        assert exc_info.value.status == 0
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio()
async def test_post_token_refuses_unsafe_url() -> None:
    # ``_post_token`` is a protected coroutine on the base class — invoking it
    # via the abstract type isn't possible, so use a minimal concrete stub
    # that keeps every abstract method as a raiser (unused here).
    class _StubProvider(OAuth2Provider):
        name = "stub"
        managed = False

        async def begin_connect(self, ctx, *, api, request):
            raise NotImplementedError

        async def complete_connect(self, ctx, *, state, callback):
            raise NotImplementedError

        async def refresh(self, ctx, *, token):
            raise NotImplementedError

    provider = _StubProvider()
    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(TokenExchangeError) as exc_info:
            await provider._post_token(_UNSAFE_URL, {"grant_type": "refresh_token"})
        assert exc_info.value.status == 0
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio()
async def test_echo_identity_refuses_unsafe_url() -> None:
    probe = VendorIdentityProbeConfig(
        endpoint=_UNSAFE_URL,
        identity_field="login",
        display_template="@{login}",
    )
    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(IdentityEchoError):
            await echo_identity(probe=probe, access_token="tok")
        mock_client_cls.assert_not_called()


class _StubOAuth2Provider(OAuth2Provider):
    name = "stub"
    managed = False

    async def begin_connect(self, ctx, *, api, request):
        raise NotImplementedError

    async def complete_connect(self, ctx, *, state, callback):
        raise NotImplementedError

    async def refresh(self, ctx, *, token):
        raise NotImplementedError


_VENDOR_URL = "https://vendor.example.com/endpoint"


async def _call_begin() -> Any:
    return await begin_device_authorization(
        authorization_endpoint=_VENDOR_URL, client_id="cid", scopes=[]
    )


async def _call_poll() -> Any:
    return await poll_device_authorization(
        token_endpoint=_VENDOR_URL, client_id="cid", device_code="dc"
    )


async def _call_post_token() -> Any:
    return await _StubOAuth2Provider()._post_token(_VENDOR_URL, {"grant_type": "refresh_token"})


async def _call_echo() -> Any:
    probe = VendorIdentityProbeConfig(
        endpoint=_VENDOR_URL, identity_field="login", display_template="@{login}"
    )
    return await echo_identity(probe=probe, access_token="tok")


_CONNECT_SITES: list[tuple[Callable[[], Awaitable[Any]], type[Exception]]] = [
    (_call_begin, DeviceAuthorizationUpstreamError),
    (_call_poll, DeviceAuthorizationUpstreamError),
    (_call_post_token, TokenExchangeError),
    (_call_echo, IdentityEchoError),
]


def _assert_refused_before_request(exc: Exception) -> None:
    """The site's typed error carries the local guard message and no HTTP status."""
    detail = f"{exc} {getattr(exc, 'body', '')}"
    assert "unsafe upstream URL" in detail
    assert getattr(exc, "status", 0) == 0


def _addrinfo(ip: str) -> list[tuple[Any, ...]]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]


@pytest.mark.asyncio()
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
async def test_connect_site_refuses_host_rebinding_to_blocked_address(
    call: Callable[[], Awaitable[Any]], error: type[Exception]
) -> None:
    # Pre-flight validation sees a public address; the connect-time resolution
    # answers with an embedded loopback address. The pinning transport refuses
    # it before any socket is opened.
    answers = iter([_addrinfo("93.184.216.34"), _addrinfo("::ffff:127.0.0.1")])
    with (
        patch.object(socket, "getaddrinfo", side_effect=lambda *_a, **_k: next(answers)),
        pytest.raises(error) as exc_info,
    ):
        await call()
    _assert_refused_before_request(exc_info.value)


@pytest.mark.asyncio()
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
async def test_connect_site_fails_closed_on_unresolvable_host(
    call: Callable[[], Awaitable[Any]], error: type[Exception]
) -> None:
    # Pre-flight validation lets an unresolvable name through; the connection
    # itself must not proceed.
    with (
        patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")),
        pytest.raises(error) as exc_info,
    ):
        await call()
    _assert_refused_before_request(exc_info.value)
