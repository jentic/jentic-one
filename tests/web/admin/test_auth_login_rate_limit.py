"""Web tests for the ``POST /auth/login`` rate limits (#1441).

Pins the per-caller-IP and per-account-email token buckets: an exhausted
bucket answers a ``429 rate_limit_exceeded`` RFC 9457 problem with
``Retry-After`` before any credential check runs, so throttled attempts never
reach the failed-login counter.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.repos import UserSecretRepository
from jentic_one.shared.config import AdminLoginRateLimitConfig
from jentic_one.shared.context import Context

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = pytest.mark.integration

_WRONG = "wrong-password"  # pragma: allowlist secret
# Effectively no refill inside a test run, so bucket capacity alone decides.
_SLOW_RPM = 1
_ROOMY = 1000


def _set_limits(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Context,
    *,
    enabled: bool = True,
    ip_rpm: int = _ROOMY,
    ip_burst: int = _ROOMY,
    email_rpm: int = _ROOMY,
    email_burst: int = _ROOMY,
) -> None:
    monkeypatch.setattr(
        ctx.config.admin.auth,
        "login_rate_limit",
        AdminLoginRateLimitConfig(
            enabled=enabled,
            ip_rpm=ip_rpm,
            ip_burst=ip_burst,
            email_rpm=email_rpm,
            email_burst=email_burst,
        ),
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


async def _failed_count(ctx: Context, user_id: str) -> int:
    async with ctx.admin_db.session() as session:
        secret: UserSecret | None = await UserSecretRepository.get_by_user_id(session, user_id)
    assert secret is not None
    return secret.failed_login_count


async def test_login_per_ip_bucket_returns_429_after_burst(
    web_context: Context,
    unauthed_client: TestClient,
    admin_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempts across different emails share one caller-IP bucket."""
    _set_limits(monkeypatch, web_context, ip_rpm=_SLOW_RPM, ip_burst=3)

    for i in range(3):
        resp = unauthed_client.post(
            "/auth/login", json={"email": f"spray-{i}@example.com", "password": _WRONG}
        )
        assert resp.status_code == 401

    # A fresh email does not help: the IP bucket is dry — even for valid credentials.
    resp = unauthed_client.post(
        "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    _assert_rate_limited(resp)
    # Throttled before the credential check: the counter was never touched.
    assert await _failed_count(web_context, admin_user_id) == 0


async def test_login_per_email_bucket_returns_429_and_skips_credential_check(
    web_context: Context,
    unauthed_client: TestClient,
    admin_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case variants of one email share a bucket; other emails are unaffected."""
    _set_limits(monkeypatch, web_context, email_rpm=_SLOW_RPM, email_burst=2)

    for email in (ADMIN_EMAIL, ADMIN_EMAIL.upper()):
        resp = unauthed_client.post("/auth/login", json={"email": email, "password": _WRONG})
        assert resp.status_code == 401
    assert await _failed_count(web_context, admin_user_id) == 2

    resp = unauthed_client.post(
        "/auth/login", json={"email": f"  {ADMIN_EMAIL.title()} ", "password": _WRONG}
    )
    _assert_rate_limited(resp)
    # The throttled attempt never reached authenticate(), so it was not counted.
    assert await _failed_count(web_context, admin_user_id) == 2

    # A different account email still gets a credential verdict, not a 429.
    resp = unauthed_client.post(
        "/auth/login", json={"email": "someone-else@example.com", "password": _WRONG}
    )
    assert resp.status_code == 401


async def test_login_rate_limit_disabled_never_throttles(
    web_context: Context,
    unauthed_client: TestClient,
    admin_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``enabled: false`` turns both buckets off."""
    _set_limits(
        monkeypatch, web_context, enabled=False, ip_rpm=_SLOW_RPM, ip_burst=1, email_burst=1
    )

    for _ in range(3):
        resp = unauthed_client.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert resp.status_code == 200
