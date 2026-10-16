"""Web tests for the ``POST /auth/login`` rate limits (#1441).

Pins the per-caller-IP and per-account-email token buckets: an exhausted
bucket answers a ``429 rate_limit_exceeded`` RFC 9457 problem with
``Retry-After`` before any credential check runs, so throttled attempts never
reach the failed-login counter.
"""

from __future__ import annotations

import pytest
import structlog.testing
from fastapi.testclient import TestClient
from httpx2 import Response

from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.repos import UserSecretRepository
from jentic_one.shared.auth.login_metrics import login_counter
from jentic_one.shared.config import AdminLoginRateLimitConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.login_rate_limit import LOGIN_RATE_LIMIT_DETAIL

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, _build_app

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


async def test_login_rate_limit_config_change_applies_after_first_request(
    web_context: Context,
    unauthed_client: TestClient,
    admin_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Limiters are cached per effective config, so a tightened limit takes effect at once."""
    _set_limits(monkeypatch, web_context)
    resp = unauthed_client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": _WRONG})
    assert resp.status_code == 401

    _set_limits(monkeypatch, web_context, email_rpm=_SLOW_RPM, email_burst=1)
    resp = unauthed_client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": _WRONG})
    assert resp.status_code == 401
    resp = unauthed_client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": _WRONG})
    _assert_rate_limited(resp)


async def test_login_ip_bucket_keys_on_forwarded_client_behind_cidr_proxy(
    web_context: Context,
    admin_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind a CIDR-listed proxy each forwarded client gets its own IP bucket.

    The documented Helm form (``trusted_proxies: 10.0.0.0/8``) must match the
    ingress pod's socket IP; otherwise every client shares the proxy's bucket
    and one caller throttles everyone's login.
    """
    _set_limits(monkeypatch, web_context, ip_rpm=_SLOW_RPM, ip_burst=1)
    monkeypatch.setattr(web_context.config.auth.oauth_rate_limit, "trusted_proxies", ["10.0.0.0/8"])
    app = _build_app(web_context)
    with TestClient(app, client=("10.1.2.3", 4321)) as proxied:
        first = {"x-forwarded-for": "203.0.113.9"}
        assert _login(proxied, first).status_code == 401
        _assert_rate_limited(_login(proxied, first))
        # A different forwarded client is not throttled by the first one's burst.
        assert _login(proxied, {"x-forwarded-for": "198.51.100.20"}).status_code == 401
        # IPv6 callers share one bucket per /64.
        assert _login(proxied, {"x-forwarded-for": "2001:db8:1:2::1"}).status_code == 401
        _assert_rate_limited(_login(proxied, {"x-forwarded-for": "2001:db8:1:2::ffff"}))


async def test_login_ip_bucket_ignores_spoofed_xff_from_untrusted_socket(
    web_context: Context,
    admin_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller outside ``trusted_proxies`` cannot mint fresh buckets via ``X-Forwarded-For``.

    The denial is also observable: one ``outcome=rate_limited`` login count and
    a ``login_rate_limited`` warning that names the bucket without the raw IP.
    """
    _set_limits(monkeypatch, web_context, ip_rpm=_SLOW_RPM, ip_burst=1)
    monkeypatch.setattr(web_context.config.auth.oauth_rate_limit, "trusted_proxies", ["10.0.0.0/8"])
    recorded: list[tuple[int, dict[str, str]]] = []
    monkeypatch.setattr(
        login_counter,
        "add",
        lambda amount, attributes=None: recorded.append((amount, dict(attributes or {}))),
    )
    app = _build_app(web_context)
    with (
        TestClient(app, client=("192.0.2.50", 4321)) as direct,
        structlog.testing.capture_logs() as logs,
    ):
        assert _login(direct, {"x-forwarded-for": "203.0.113.1"}).status_code == 401
        _assert_rate_limited(_login(direct, {"x-forwarded-for": "203.0.113.2"}))

    assert recorded == [(1, {"outcome": "rate_limited", "bucket": "ip"})]
    denied = [log for log in logs if log["event"] == "login_rate_limited"]
    assert len(denied) == 1
    assert denied[0]["bucket"] == "ip"
    assert len(denied[0]["key_ref"]) == 12
    assert "192.0.2.50" not in repr(denied[0])


def test_login_openapi_429_matches_runtime_problem(
    web_context: Context, unauthed_client: TestClient
) -> None:
    """The spec's 429 example carries the handler's real detail and declares the headers."""
    spec = unauthed_client.app.openapi()  # type: ignore[attr-defined]
    resp_429 = spec["paths"]["/auth/login"]["post"]["responses"]["429"]
    example = resp_429["content"]["application/problem+json"]["example"]
    assert example["detail"] == LOGIN_RATE_LIMIT_DETAIL
    assert example["instance"] == "/auth/login"
    assert example["type"] == "rate_limit_exceeded"
    assert set(resp_429["headers"]) == {"Retry-After", "RateLimit-Limit", "RateLimit-Remaining"}


def _login(client: TestClient, headers: dict[str, str]) -> Response:
    return client.post(
        "/auth/login", json={"email": "spray@example.com", "password": _WRONG}, headers=headers
    )
