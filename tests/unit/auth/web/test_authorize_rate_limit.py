"""The auth surface's pre-auth rate limiters key on the client network only (#1874).

Covers the authorization-flow limiter over real routes (``GET /authorize``,
``POST /oauth/consent``): a caller-chosen ``client_id`` never opens a fresh
bucket, an IPv6 host cannot rotate interface IDs inside its /64 into fresh
buckets, distinct networks keep independent buckets, and ``X-Forwarded-For``
counts only from a trusted proxy. Also pins the same network grouping on
every other anonymous auth limiter and the normalisation helper itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web import flow
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.ratelimit import client_network
from jentic_one.auth.web.routers import authorize, local_login, oauth, oauth_client_registration
from jentic_one.shared.config import AuthConfig, OAuthRateLimitConfig
from jentic_one.shared.context import Context
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.web.deps import get_ctx

_BURST = 3
_AUTHORIZE_PARAMS = {
    "response_type": "code",
    "redirect_uri": "https://app.example.com/cb",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",  # pragma: allowlist secret
    "code_challenge_method": "S256",
    "scope": "apis:read",
    "state": "client-state-1",
}
_PROXY = "10.0.0.2"


def _rate_limit(*, trusted_proxies: list[str] | None = None) -> OAuthRateLimitConfig:
    """Every anonymous auth limiter at burst ``_BURST`` and a negligible refill."""
    return OAuthRateLimitConfig(
        authorize_rpm=1,
        authorize_burst=_BURST,
        exchange_rpm=1,
        exchange_burst=_BURST,
        registration_rpm=1,
        registration_burst=_BURST,
        approval_status_rpm=1,
        approval_status_burst=_BURST,
        trusted_proxies=trusted_proxies or [],
    )


def _make_app(*, trusted_proxies: list[str] | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(authorize.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        oauth_rate_limit=_rate_limit(trusted_proxies=trusted_proxies),
    )
    ctx.config.admin.auth.jwt_secret.get_secret_value = MagicMock(return_value="secret")
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    return app


@pytest.fixture(autouse=True)
def _unknown_clients() -> Iterator[None]:
    """Every ``client_id`` resolves to no registered client."""
    svc = MagicMock()
    svc.get_by_client_id = AsyncMock(return_value=None)
    with patch.object(flow, "OAuthClientService", return_value=svc):
        yield


def _client_at(app: FastAPI, host: str) -> TestClient:
    return TestClient(app, client=(host, 50000), follow_redirects=False)


def _authorize(client: TestClient, client_id: str, **headers: str) -> int:
    params = {**_AUTHORIZE_PARAMS, "client_id": client_id}
    return client.get("/authorize", params=params, headers=headers).status_code


def _consent(client: TestClient, client_id: str) -> int:
    return client.post(
        "/oauth/consent",
        params={"client_id": client_id},
        data={"consent_token": "not-a-handle", "action": "approve"},
    ).status_code


def _assert_limited_after_burst(statuses: list[int]) -> None:
    """The first ``_BURST`` requests reach the handler, then the bucket is dry."""
    assert len(statuses) == _BURST + 1
    assert 429 not in statuses[:_BURST]
    assert len(set(statuses[:_BURST])) == 1, statuses
    assert statuses[_BURST] == 429


@pytest.mark.parametrize("send", [_authorize, _consent], ids=["authorize", "consent"])
def test_rotating_client_id_does_not_open_a_fresh_bucket(
    send: Callable[[TestClient, str], int],
) -> None:
    """A fresh ``client_id`` per request still drains one network's bucket."""
    client = _client_at(_make_app(), "203.0.113.7")
    _assert_limited_after_burst([send(client, f"oc_rotated_{i}") for i in range(_BURST + 1)])


def test_the_whole_flow_shares_one_bucket_per_network() -> None:
    """``/authorize`` and the consent submit spend the same bucket, and the
    429 carries ``Retry-After``."""
    client = _client_at(_make_app(), "203.0.113.7")
    assert _authorize(client, "oc_a") != 429
    assert _consent(client, "oc_b") != 429
    assert _authorize(client, "oc_c") != 429
    limited = client.post(
        "/oauth/consent", data={"consent_token": "not-a-handle", "action": "approve"}
    )
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


@pytest.mark.parametrize("send", [_authorize, _consent], ids=["authorize", "consent"])
def test_rotating_the_ipv6_interface_id_does_not_open_a_fresh_bucket(
    send: Callable[[TestClient, str], int],
) -> None:
    """Every address in one /64 shares a bucket; the next /64 has its own."""
    app = _make_app()
    statuses = [send(_client_at(app, f"2001:db8:1:2::{i + 1:x}"), "oc_test") for i in range(_BURST)]
    statuses.append(send(_client_at(app, "2001:db8:1:2:ffff:ffff:ffff:fffe"), "oc_test"))
    _assert_limited_after_burst(statuses)
    assert send(_client_at(app, "2001:db8:1:3::1"), "oc_test") != 429


@pytest.mark.parametrize(
    ("first", "second", "shared"),
    [
        ("203.0.113.7", "203.0.113.8", False),
        ("::ffff:203.0.113.7", "203.0.113.7", True),
        ("2001:db8:1:2::1", "2001:db8:1:3::1", False),
    ],
)
def test_distinct_networks_keep_independent_buckets(first: str, second: str, shared: bool) -> None:
    """Draining one network's bucket leaves another network's untouched; an
    IPv4-mapped address is the same client as its IPv4 form."""
    app = _make_app()
    drained = _client_at(app, first)
    for _ in range(_BURST):
        assert _authorize(drained, "oc_test") != 429
    assert _authorize(drained, "oc_test") == 429
    assert (_authorize(_client_at(app, second), "oc_test") == 429) is shared


def test_untrusted_forwarded_for_is_ignored() -> None:
    """Rotating ``X-Forwarded-For`` from a socket that is not a listed proxy
    leaves the caller in its socket address's bucket."""
    client = _client_at(_make_app(trusted_proxies=[_PROXY]), "203.0.113.7")
    statuses = [
        _authorize(client, f"oc_{i}", **{"X-Forwarded-For": f"192.0.2.{i}"})
        for i in range(_BURST + 1)
    ]
    _assert_limited_after_burst(statuses)


def test_trusted_proxy_forwarded_for_keys_on_the_forwarded_network() -> None:
    """Behind a listed proxy, each forwarded client network has its own
    bucket, and a forwarded ``:port`` or interface-ID rotation within a /64
    does not split one."""
    via_proxy = _client_at(_make_app(trusted_proxies=[_PROXY]), _PROXY)
    hops = ["[2001:db8:1:2::1]:443", "2001:db8:1:2::2", "[2001:db8:1:2::3]:51234"]
    statuses = [_authorize(via_proxy, "oc_test", **{"X-Forwarded-For": hop}) for hop in hops]
    statuses.append(
        _authorize(via_proxy, "oc_test", **{"X-Forwarded-For": "2001:db8:1:2::4, " + _PROXY})
    )
    _assert_limited_after_burst(statuses)
    assert _authorize(via_proxy, "oc_test", **{"X-Forwarded-For": "198.51.100.9"}) != 429


# --- the other anonymous auth limiters ------------------------------------------


async def _revocation(request: Request, ctx: Annotated[Context, Depends(get_ctx)]) -> None:
    await oauth._check_revocation_rate_limit(request, ctx)


_LIMITERS: dict[str, Callable[..., Awaitable[None]]] = {
    "authorize": flow.check_rate_limit,
    "approval-status": flow.check_approval_status_rate_limit,
    "registration": oauth_client_registration._check_registration_rate_limit,
    "session-continue": local_login.check_session_continue_rate_limit,
    "revocation": _revocation,
    "token": oauth._check_token_rate_limit,
}


@pytest.mark.parametrize("dependency", list(_LIMITERS.values()), ids=list(_LIMITERS))
def test_every_anonymous_auth_limiter_groups_ipv6_by_64(
    dependency: Callable[..., Awaitable[None]],
) -> None:
    """Each pre-auth limiter shares one bucket across a /64 and keeps the
    next /64 separate."""
    app = _make_app()
    app.post("/probe", dependencies=[Depends(dependency)])(lambda: {})

    def probe(host: str) -> int:
        return TestClient(app, client=(host, 50000)).post("/probe").status_code

    statuses = [probe(f"2001:db8:1:2::{i + 1:x}") for i in range(_BURST + 1)]
    assert statuses == [200] * _BURST + [429]
    assert probe("2001:db8:1:3::1") == 200


def test_the_authorize_bucket_is_not_shared_with_the_token_endpoint() -> None:
    """A network that drained the token endpoint's ``client_id``-less bucket
    still reaches ``/authorize``: the two limiters have different rate/burst
    params and live in separate namespaces."""
    app = _make_app()
    app.post("/token-probe", dependencies=[Depends(oauth._check_token_rate_limit)])(lambda: {})
    client = _client_at(app, "203.0.113.7")
    statuses = [client.post("/token-probe").status_code for _ in range(_BURST + 1)]
    assert statuses == [200] * _BURST + [429]
    assert _authorize(client, "oc_test") != 429


# --- client_network -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "network"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("203.0.113.7:51234", "203.0.113.7"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("[::ffff:203.0.113.7]:443", "203.0.113.7"),
        ("64:ff9b::203.0.113.7", "203.0.113.7"),
        ("64:ff9b:1::203.0.113.7", "64:ff9b:1::/64"),
        ("2002:cb00:7107:1::1", "203.0.113.7"),
        ("2002:cb00:7107:2::1", "203.0.113.7"),
        ("2001:0:4136:e378:8000:63bf:34ff:8ef8", "203.0.113.7"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:DB8:1:2::0:1", "2001:db8:1:2::/64"),
        ("[2001:db8:1:2::1]:443", "2001:db8:1:2::/64"),
        ("fe80::1%eth0", "fe80::/64"),
        ("fd00:1:2:3::1", "fd00:1:2:3::/64"),
        ("::1", "::/64"),
        ("unknown", "unknown"),
        ("not an address", "not an address"),
    ],
)
def test_client_network_normalises_addresses(value: str, network: str) -> None:
    """A port is dropped, an IPv6 address that embeds an IPv4 client
    (IPv4-mapped, well-known-prefix NAT64, 6to4, Teredo) is that IPv4 client,
    any other IPv6 address (link-local, unique-local and a network-specific
    translation prefix included) is grouped by its /64, and a non-address is
    passed through."""
    assert client_network(value) == network
