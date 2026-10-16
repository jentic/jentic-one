"""SSRF guard pins for the vendor-facing calls in the connect flow.

Every raw ``httpx`` call in the connect flow (device authorization, token
exchange, identity echo) is fronted by ``validate_upstream_url_async`` so that a
mis-configured or tampered upstream cannot aim a platform-issued request at a
private / loopback / cloud-metadata target. These tests pin the guard at each
call site: an unsafe URL must raise the site's typed error BEFORE any HTTP is
opened. Each site also connects through the always-on DNS-pinning transport, so a
host that re-resolves to a blocked address — or does not resolve at all — at
connect time is refused with the same typed error. An operator's control-plane
egress allowlist applies to both checks, and nothing beyond it.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

import httpx
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
from jentic_one.shared.config import EgressConfig, VendorIdentityProbeConfig

# Loopback address — the strict-default egress policy (``egress=None``) rejects
# the whole 127.0.0.0/8 range. Any of the blocked ranges works; loopback is the
# most obvious "should never legitimately be a vendor".
_UNSAFE_URL = "http://127.0.0.1/x"


@pytest.mark.asyncio()
async def test_begin_device_authorization_refuses_unsafe_url() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(DeviceAuthorizationUpstreamError) as exc_info:
            await begin_device_authorization(
                authorization_endpoint=_UNSAFE_URL,
                client_id="cid",
                scopes=[],
                egress=None,
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
                egress=None,
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
            await provider._post_token(_UNSAFE_URL, {"grant_type": "refresh_token"}, egress=None)
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
            await echo_identity(probe=probe, access_token="tok", egress=None)
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


async def _call_begin(url: str = _VENDOR_URL, egress: EgressConfig | None = None) -> Any:
    return await begin_device_authorization(
        authorization_endpoint=url, client_id="cid", scopes=[], egress=egress
    )


async def _call_poll(url: str = _VENDOR_URL, egress: EgressConfig | None = None) -> Any:
    return await poll_device_authorization(
        token_endpoint=url, client_id="cid", device_code="dc", egress=egress
    )


async def _call_post_token(url: str = _VENDOR_URL, egress: EgressConfig | None = None) -> Any:
    return await _StubOAuth2Provider()._post_token(
        url, {"grant_type": "refresh_token"}, egress=egress
    )


async def _call_echo(url: str = _VENDOR_URL, egress: EgressConfig | None = None) -> Any:
    probe = VendorIdentityProbeConfig(
        endpoint=url, identity_field="login", display_template="@{login}"
    )
    return await echo_identity(probe=probe, access_token="tok", egress=egress)


_CONNECT_SITES: list[tuple[Callable[..., Awaitable[Any]], type[Exception]]] = [
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
async def test_connect_site_preflight_resolves_off_the_event_loop(
    call: Callable[[], Awaitable[Any]], error: type[Exception]
) -> None:
    # Pins #1734: the pre-flight DNS lookup runs in a worker thread, never on
    # the event-loop thread, and a name resolving to a blocked range is still
    # refused before any request is sent.
    loop_thread = threading.get_ident()
    lookup_threads: list[int] = []

    def _resolve(*_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        lookup_threads.append(threading.get_ident())
        return _addrinfo("10.0.0.1")

    with (
        patch.object(socket, "getaddrinfo", side_effect=_resolve),
        pytest.raises(error) as exc_info,
    ):
        await call()
    _assert_refused_before_request(exc_info.value)
    assert lookup_threads
    assert loop_thread not in lookup_threads


@pytest.mark.asyncio()
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
@pytest.mark.usefixtures("uncached_dns")
async def test_connect_site_refuses_host_rebinding_to_blocked_address(
    call: Callable[..., Awaitable[Any]], error: type[Exception]
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
    call: Callable[..., Awaitable[Any]], error: type[Exception]
) -> None:
    # Pre-flight validation lets an unresolvable name through; the connection
    # itself must not proceed.
    with (
        patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")),
        pytest.raises(error) as exc_info,
    ):
        await call()
    _assert_refused_before_request(exc_info.value)


_INTERNAL_URL = "https://idp.corp.internal/endpoint"
_ALLOWLIST = EgressConfig(
    allowed_private_subnets=["10.50.0.0/16"], allowed_internal_domains=[".corp.internal"]
)


def _vendor_reply(request: httpx.Request) -> httpx.Response:
    # One body that satisfies every site: device authorization, token, identity.
    return httpx.Response(
        200,
        json={
            "device_code": "dc",
            "user_code": "UC",
            "verification_uri": "https://idp.corp.internal/device",
            "access_token": "at",
            "login": "octo",
        },
    )


@pytest.mark.asyncio()
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
async def test_connect_site_reaches_allowlisted_internal_host_pinned(
    call: Callable[..., Awaitable[Any]], error: type[Exception]
) -> None:
    # The operator allowlisted the subnet + domain: both the pre-flight check
    # and the connect-time pin accept it, and the connection is pinned to the
    # validated IP with the original name kept for Host / SNI.
    sent: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _vendor_reply(request)

    with (
        patch.object(socket, "getaddrinfo", return_value=_addrinfo("10.50.0.7")),
        patch.object(
            httpx, "AsyncHTTPTransport", side_effect=lambda *a, **k: httpx.MockTransport(_record)
        ),
    ):
        await call(_INTERNAL_URL, _ALLOWLIST)
    assert len(sent) == 1
    assert sent[0].url.host == "10.50.0.7"
    assert sent[0].headers["host"] == "idp.corp.internal"


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "egress",
    [
        None,
        EgressConfig(allowed_private_subnets=["10.60.0.0/16"]),
        EgressConfig(
            allowed_private_subnets=["10.50.0.0/16"], allowed_internal_domains=[".other.internal"]
        ),
    ],
    ids=["no-allowlist", "subnet-not-allowlisted", "domain-not-allowlisted"],
)
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
async def test_connect_site_refuses_internal_host_outside_allowlist(
    call: Callable[..., Awaitable[Any]], error: type[Exception], egress: EgressConfig | None
) -> None:
    with (
        patch.object(socket, "getaddrinfo", return_value=_addrinfo("10.50.0.7")),
        patch("httpx.AsyncClient") as mock_client_cls,
        pytest.raises(error) as exc_info,
    ):
        await call(_INTERNAL_URL, egress)
    _assert_refused_before_request(exc_info.value)
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio()
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
@pytest.mark.usefixtures("uncached_dns")
async def test_connect_site_pin_refuses_rebind_outside_allowlist(
    call: Callable[..., Awaitable[Any]], error: type[Exception]
) -> None:
    # Pre-flight sees an allowlisted address; connect time answers with a
    # private address the allowlist does not cover.
    answers = [_addrinfo("10.50.0.7"), _addrinfo("10.60.0.9")]
    served: list[list[tuple[Any, ...]]] = []

    def _resolve(*_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        served.append(answers[len(served)])
        return served[-1]

    with (
        patch.object(socket, "getaddrinfo", side_effect=_resolve),
        pytest.raises(error) as exc_info,
    ):
        await call(_INTERNAL_URL, _ALLOWLIST)
    _assert_refused_before_request(exc_info.value)
    # Both answers were handed out: pre-flight accepted the first, the pin
    # refused the second (no cached answer bridged the two lookups).
    assert served == answers


@pytest.mark.asyncio()
@pytest.mark.parametrize(("call", "error"), _CONNECT_SITES)
async def test_connect_site_applies_allowlist_off_the_event_loop(
    call: Callable[..., Awaitable[Any]], error: type[Exception]
) -> None:
    # The allowlist is honoured on the async validation path: every lookup for
    # the allowlisted internal host runs in a worker thread and the request is
    # still sent.
    loop_thread = threading.get_ident()
    lookup_threads: list[int] = []
    sent: list[httpx.Request] = []

    def _resolve(*_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        lookup_threads.append(threading.get_ident())
        return _addrinfo("10.50.0.7")

    def _record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _vendor_reply(request)

    with (
        patch.object(socket, "getaddrinfo", side_effect=_resolve),
        patch.object(
            httpx, "AsyncHTTPTransport", side_effect=lambda *a, **k: httpx.MockTransport(_record)
        ),
    ):
        await call(_INTERNAL_URL, _ALLOWLIST)
    assert len(sent) == 1
    assert lookup_threads
    assert loop_thread not in lookup_threads
