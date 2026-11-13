"""Integration tests for ``POST /auth/login`` per-client-network rate limiting (#1441).

Pins that ``POST /auth/login`` throttles bursts per client network (IPv4
address or IPv6 ``/64``) over the process's shared-state backend before any
credential check runs, preserves generic ``401 invalid_credentials`` responses
and dummy-verify timing equalization, and leaves atomic per-account lockout
intact within valid rate-limit budgets.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import structlog.testing
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete

from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services import auth_service as auth_service_module
from jentic_one.admin.services._support.passwords import (
    dummy_verify_password,
    hash_password,
    verify_password,
)
from jentic_one.admin.services.metrics import login_counter
from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.shared.config import OAuthRateLimitConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from jentic_one.shared.web.app_factory import create_combined_app

pytestmark = pytest.mark.integration

_LOGIN_EMAIL = "login-rl-admin@test.local"
_LOGIN_PASSWORD = "correct-horse-battery"  # pragma: allowlist secret
_WRONG_PASSWORD = "wrong-password-guess"  # pragma: allowlist secret
_BURST = 3


@pytest.fixture()
async def login_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """Seed an active admin user with a known password and clean up afterwards."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email=_LOGIN_EMAIL,
            first_name="Rate",
            last_name="Limited",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password(_LOGIN_PASSWORD),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"org:admin"}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


def _set_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Context,
    *,
    rpm: int = 1,
    burst: int = _BURST,
    trusted_proxies: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        ctx.config.auth,
        "oauth_rate_limit",
        OAuthRateLimitConfig(
            authorize_rpm=rpm,
            authorize_burst=burst,
            trusted_proxies=trusted_proxies or [],
        ),
    )


def _client_at(app: FastAPI, host: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(host, 50000)),
        base_url="http://testserver",
    )


def _assert_rate_limited(resp: Response) -> None:
    assert resp.status_code == 429
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "rate_limit_exceeded"
    assert body["status"] == 429
    assert body["instance"] == "/auth/login"
    assert int(resp.headers["retry-after"]) >= 1
    assert resp.headers["ratelimit-remaining"] == "0"


async def _failed_count_and_lock(ctx: Context, user_id: str) -> tuple[int, object]:
    async with ctx.admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
        assert secret is not None
        return secret.failed_login_count, secret.locked_until


async def test_login_spraying_distinct_emails_from_one_network_returns_429(
    integration_context: Context,
    login_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempts across distinct emails/passwords from one client network share a bucket."""
    _set_rate_limit(monkeypatch, integration_context, burst=_BURST)
    app = create_admin_app(integration_context)

    async with _client_at(app, "203.0.113.7") as client:
        for i in range(_BURST):
            resp = await client.post(
                "/auth/login",
                json={"email": f"spray-{i}@example.com", "password": f"{_WRONG_PASSWORD}-{i}"},
            )
            assert resp.status_code == 401

        # Exhausted bucket rejects before credential verification, even for valid credentials.
        throttled = await client.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _LOGIN_PASSWORD},
        )
        _assert_rate_limited(throttled)

    count, locked_until = await _failed_count_and_lock(integration_context, login_user)
    assert count == 0
    assert locked_until is None

    # Another client network retains its full budget.
    async with _client_at(app, "198.51.100.42") as other_client:
        ok = await other_client.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _LOGIN_PASSWORD},
        )
        assert ok.status_code == 200
        assert ok.json()["access_token"]


async def test_login_ipv6_addresses_in_same_64_share_bucket_while_other_64_is_independent(
    integration_context: Context,
    login_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct interface IDs inside one IPv6 ``/64`` share a bucket; another ``/64`` does not."""
    _set_rate_limit(monkeypatch, integration_context, burst=_BURST)
    app = create_admin_app(integration_context)

    same_subnet_hosts = [
        "2001:db8:1:2::1",
        "2001:db8:1:2:aaaa::2",
        "2001:db8:1:2:ffff:ffff:ffff:fffe",
    ]
    for i, host in enumerate(same_subnet_hosts):
        async with _client_at(app, host) as client:
            resp = await client.post(
                "/auth/login",
                json={"email": f"v6-spray-{i}@example.com", "password": _WRONG_PASSWORD},
            )
            assert resp.status_code == 401

    async with _client_at(app, "2001:db8:1:2:dead:beef::99") as fourth_in_subnet:
        throttled = await fourth_in_subnet.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _LOGIN_PASSWORD},
        )
        _assert_rate_limited(throttled)

    async with _client_at(app, "2001:db8:1:3::1") as other_subnet:
        ok = await other_subnet.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _LOGIN_PASSWORD},
        )
        assert ok.status_code == 200


async def test_login_trusted_proxy_cidr_and_untrusted_xff_spoofing(
    integration_context: Context,
    login_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trusted proxy CIDRs separate forwarded networks; untrusted XFF headers are ignored."""
    _set_rate_limit(monkeypatch, integration_context, burst=1, trusted_proxies=["10.0.0.0/8"])
    recorded: list[tuple[int, dict[str, str]]] = []
    monkeypatch.setattr(
        login_counter,
        "add",
        lambda amount, attributes=None: recorded.append((amount, dict(attributes or {}))),
    )
    app = create_admin_app(integration_context)

    # Behind a trusted proxy in 10.0.0.0/8, forwarded clients get per-network buckets.
    async with _client_at(app, "10.1.2.3") as proxied:
        r1 = await proxied.post(
            "/auth/login",
            json={"email": "a@example.com", "password": _WRONG_PASSWORD},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert r1.status_code == 401
        r2 = await proxied.post(
            "/auth/login",
            json={"email": "b@example.com", "password": _WRONG_PASSWORD},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        _assert_rate_limited(r2)

        # Distinct forwarded IPv4 has its own bucket.
        r3 = await proxied.post(
            "/auth/login",
            json={"email": "c@example.com", "password": _WRONG_PASSWORD},
            headers={"X-Forwarded-For": "198.51.100.20"},
        )
        assert r3.status_code == 401

        # Forwarded IPv6 addresses in one /64 share one bucket.
        r4 = await proxied.post(
            "/auth/login",
            json={"email": "d@example.com", "password": _WRONG_PASSWORD},
            headers={"X-Forwarded-For": "[2001:db8:9:a::1]:443"},
        )
        assert r4.status_code == 401
        r5 = await proxied.post(
            "/auth/login",
            json={"email": "e@example.com", "password": _WRONG_PASSWORD},
            headers={"X-Forwarded-For": "2001:db8:9:a::ffff"},
        )
        _assert_rate_limited(r5)

    # Direct caller outside trusted_proxies cannot rotate X-Forwarded-For to escape its bucket.
    recorded.clear()
    async with _client_at(app, "192.0.2.50") as direct:
        with structlog.testing.capture_logs() as logs:
            first = await direct.post(
                "/auth/login",
                json={"email": "f@example.com", "password": _WRONG_PASSWORD},
                headers={"X-Forwarded-For": "203.0.113.100"},
            )
            assert first.status_code == 401
            second = await direct.post(
                "/auth/login",
                json={"email": "g@example.com", "password": _WRONG_PASSWORD},
                headers={"X-Forwarded-For": "203.0.113.101"},
            )
            _assert_rate_limited(second)

    assert (1, {"outcome": "rate_limited"}) in recorded
    denied = [log for log in logs if log.get("event") == "login_rate_limited"]
    assert len(denied) == 1
    assert denied[0]["path"] == "/auth/login"
    assert "192.0.2.50" not in repr(denied[0])


async def test_login_preserves_non_enumerating_401_and_dummy_verify_and_skips_verify_on_429(
    integration_context: Context,
    login_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither under-budget 401s nor over-budget 429s create an account-existence oracle."""
    _set_rate_limit(monkeypatch, integration_context, burst=2)
    dummy_calls: list[int] = []
    real_calls: list[int] = []

    def _tracked_dummy() -> None:
        dummy_calls.append(1)
        dummy_verify_password()

    def _tracked_verify(plain: str, hashed: str) -> bool:
        real_calls.append(1)
        return verify_password(plain, hashed)

    monkeypatch.setattr(auth_service_module, "dummy_verify_password", _tracked_dummy)
    monkeypatch.setattr(auth_service_module, "verify_password", _tracked_verify)

    app = create_admin_app(integration_context)

    # Under budget: unknown email burns dummy_verify; existing email burns real verify;
    # both return byte-identical 401 invalid_credentials responses.
    async with _client_at(app, "203.0.113.10") as client:
        unknown = await client.post(
            "/auth/login",
            json={"email": "unknown-person@example.com", "password": _WRONG_PASSWORD},
        )
        wrong = await client.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _WRONG_PASSWORD},
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.content == wrong.content
        assert unknown.json()["type"] == "invalid_credentials"
        assert len(dummy_calls) == 1
        assert len(real_calls) == 1

        # Over budget: both existing and unknown emails return identical 429s
        # without invoking either password verifier.
        over_existing = await client.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _WRONG_PASSWORD},
        )
        over_unknown = await client.post(
            "/auth/login",
            json={"email": "unknown-person@example.com", "password": _WRONG_PASSWORD},
        )
        _assert_rate_limited(over_existing)
        _assert_rate_limited(over_unknown)
        assert over_existing.json() == over_unknown.json()
        assert len(dummy_calls) == 1
        assert len(real_calls) == 1


async def test_login_atomic_account_lockout_continues_within_rate_limit_budget(
    integration_context: Context,
    login_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within a valid rate-limit budget, concurrent wrong passwords atomically lock the account."""
    threshold = integration_context.config.admin.auth.failed_login_lockout_threshold
    _set_rate_limit(monkeypatch, integration_context, rpm=60, burst=threshold + 10)
    app = create_combined_app(integration_context, ["admin", "auth"])

    async with _client_at(app, "203.0.113.55") as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/auth/login",
                    json={"email": _LOGIN_EMAIL, "password": f"{_WRONG_PASSWORD}-{i}"},
                )
                for i in range(threshold)
            )
        )
        assert [r.status_code for r in responses] == [401] * threshold

        count, locked_until = await _failed_count_and_lock(integration_context, login_user)
        assert count == threshold
        assert locked_until is not None

        # Locked account still answers generic 401 invalid_credentials even with valid password.
        locked_resp = await client.post(
            "/auth/login",
            json={"email": _LOGIN_EMAIL, "password": _LOGIN_PASSWORD},
        )
        unknown_resp = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": _WRONG_PASSWORD},
        )
        assert locked_resp.status_code == unknown_resp.status_code == 401
        assert locked_resp.content == unknown_resp.content
        assert login_user not in locked_resp.text


async def test_login_rate_limit_config_update_applies_immediately(
    integration_context: Context,
    login_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tightening ``authorize_burst`` after the first request takes effect immediately."""
    _set_rate_limit(monkeypatch, integration_context, rpm=1, burst=50)
    app = create_admin_app(integration_context)

    async with _client_at(app, "203.0.113.88") as client:
        first = await client.post(
            "/auth/login",
            json={"email": "first@example.com", "password": _WRONG_PASSWORD},
        )
        assert first.status_code == 401

        # Tighten burst to 1: the bucket clamps to 1 remaining token, so one
        # request spends it and the next is throttled (rather than serving 48
        # more under the initial burst=50).
        _set_rate_limit(monkeypatch, integration_context, rpm=1, burst=1)
        second = await client.post(
            "/auth/login",
            json={"email": "second@example.com", "password": _WRONG_PASSWORD},
        )
        assert second.status_code == 401
        third = await client.post(
            "/auth/login",
            json={"email": "third@example.com", "password": _WRONG_PASSWORD},
        )
        _assert_rate_limited(third)
