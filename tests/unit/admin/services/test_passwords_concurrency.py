"""Unit and web tests for bounded concurrent argon2 operations (#1811)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jentic_one.admin.repos import UserRepository, UserSecretRepository
from jentic_one.admin.services import auth_service as auth_service_mod
from jentic_one.admin.services._support.passwords import (
    configure_argon2,
    dummy_verify_password_async,
    hash_password_async,
    reset_argon2_gate,
    run_argon2,
    verify_password_async,
)
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import PasswordHasherBusyError
from jentic_one.admin.web.app import get_exception_handlers as get_admin_exception_handlers
from jentic_one.admin.web.deps import get_auth_service
from jentic_one.admin.web.routers import auth as admin_auth_router
from jentic_one.auth.web.app import get_exception_handlers as get_auth_exception_handlers
from jentic_one.auth.web.flow import login_signing_key, sign_payload
from jentic_one.auth.web.routers import authorize, local_login, oauth
from jentic_one.shared.config import (
    AdminAuthConfig,
    AuthConfig,
    LocalLoginConfig,
    PlatformClientConfig,
)
from jentic_one.shared.state.backend import MemoryStateBackend


@pytest.fixture(autouse=True)
def _clean_argon2_gate() -> Iterator[None]:
    """Reset the process-wide argon2 gate before and after every test."""
    reset_argon2_gate()
    yield
    reset_argon2_gate()


@pytest.mark.asyncio
async def test_hash_and_verify_async_round_trip() -> None:
    """Async argon2 helpers hash, verify, and run the dummy equalizer under the gate."""
    hashed = await hash_password_async("S3cure-Passw0rd!")
    assert hashed.startswith("$argon2id$")
    assert await verify_password_async("S3cure-Passw0rd!", hashed) is True
    assert await verify_password_async("wrong-password", hashed) is False
    await dummy_verify_password_async()


@pytest.mark.asyncio
async def test_run_argon2_bounds_peak_concurrency() -> None:
    """At most ``max_concurrency`` argon2 tasks execute concurrently across a burst."""
    limit = 3
    total = 12
    configure_argon2(max_concurrency=limit, acquire_timeout_s=5.0)

    lock = threading.Lock()
    in_flight = 0
    peak_in_flight = 0

    def _work(idx: int) -> int:
        nonlocal in_flight, peak_in_flight
        with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        try:
            time.sleep(0.03)
            return idx
        finally:
            with lock:
                in_flight -= 1

    results = await asyncio.gather(*(run_argon2(_work, i) for i in range(total)))
    assert sorted(results) == list(range(total))
    assert peak_in_flight == limit


@pytest.mark.asyncio
async def test_run_argon2_sheds_excess_callers_past_acquire_timeout() -> None:
    """Callers waiting past ``acquire_timeout_s`` raise PasswordHasherBusyError without running."""
    limit = 2
    total = 10
    configure_argon2(max_concurrency=limit, acquire_timeout_s=0.05)

    release_gate = threading.Event()
    all_slots_taken = threading.Event()
    lock = threading.Lock()
    in_flight = 0
    peak_in_flight = 0
    executed_count = 0

    def _blocking_work() -> str:
        nonlocal in_flight, peak_in_flight, executed_count
        with lock:
            in_flight += 1
            executed_count += 1
            peak_in_flight = max(peak_in_flight, in_flight)
            if in_flight == limit:
                all_slots_taken.set()
        try:
            release_gate.wait(timeout=5.0)
            return "ok"
        finally:
            with lock:
                in_flight -= 1

    tasks = [asyncio.create_task(run_argon2(_blocking_work)) for _ in range(total)]
    try:
        await asyncio.to_thread(all_slots_taken.wait, 2.0)
        # Wait for the 8 queued callers to time out while the 2 slots stay held.
        await asyncio.sleep(0.12)
        release_gate.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        release_gate.set()

    successes = [r for r in outcomes if r == "ok"]
    busy_errors = [r for r in outcomes if isinstance(r, PasswordHasherBusyError)]

    assert len(successes) == limit
    assert len(busy_errors) == total - limit
    assert peak_in_flight == limit
    assert executed_count == limit
    assert all(err.retry_after_s == 1 for err in busy_errors)


@pytest.mark.asyncio
async def test_cancelled_caller_keeps_slot_held_until_executor_thread_finishes() -> None:
    """A cancelled coroutine does not free its slot while its thread still holds 64 MiB."""
    configure_argon2(max_concurrency=1, acquire_timeout_s=0.05)
    started = threading.Event()
    release_thread = threading.Event()
    finished = threading.Event()

    def _slow_argon2() -> None:
        started.set()
        release_thread.wait(timeout=5.0)
        finished.set()

    first = asyncio.create_task(run_argon2(_slow_argon2))
    await asyncio.to_thread(started.wait, 2.0)

    # Cancel the awaiting task while the executor thread is still running.
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first

    # Because the thread has not finished yet, the single slot is still occupied.
    with pytest.raises(PasswordHasherBusyError):
        await run_argon2(lambda: "should-not-run")

    # Once the executor thread completes, the slot is released and the next call succeeds.
    release_thread.set()
    await asyncio.to_thread(finished.wait, 2.0)
    await asyncio.sleep(0.02)
    assert await run_argon2(lambda: "recovered") == "recovered"


@pytest.mark.asyncio
async def test_concurrent_login_burst_caps_concurrency_and_returns_503_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst of N concurrent logins (N >> limit) runs at most ``limit`` argon2
    verifies at once and answers excess logins with 503 + Retry-After (#1811)."""
    limit = 2
    burst_size = 10
    auth_cfg = AdminAuthConfig(
        jwt_secret=SecretStr("burst-test-jwt-secret-key-0123456789"),
        argon2_max_concurrency=limit,
        argon2_acquire_timeout_s=0.05,
    )

    ctx = MagicMock()
    ctx.config.admin.auth = auth_cfg
    mock_read_session = AsyncMock()
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_read_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_tx_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_tx_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

    fake_user = MagicMock(
        id="usr_burst",
        email="admin@example.com",
        active=True,
        deleted_at=None,
        must_change_password=False,
        session_generation=1,
    )
    fake_secret = MagicMock(
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$fake",  # pragma: allowlist secret
        locked_until=None,
        failed_login_count=0,
    )
    monkeypatch.setattr(
        UserRepository,
        "get_by_email",
        AsyncMock(return_value=fake_user),
    )
    monkeypatch.setattr(
        UserRepository,
        "get_by_id",
        AsyncMock(return_value=fake_user),
    )
    monkeypatch.setattr(
        UserSecretRepository,
        "get_by_user_id",
        AsyncMock(return_value=fake_secret),
    )
    monkeypatch.setattr(
        UserSecretRepository,
        "reset_failed_logins",
        AsyncMock(),
    )
    monkeypatch.setattr(auth_service_mod, "record_audit", AsyncMock())
    fake_perm_svc = MagicMock()
    fake_perm_svc.get_effective_for_user = AsyncMock(
        return_value=MagicMock(effective=["org:admin"])
    )
    monkeypatch.setattr(auth_service_mod, "PermissionService", lambda _ctx: fake_perm_svc)

    release_gate = threading.Event()
    slots_filled = threading.Event()
    lock = threading.Lock()
    in_flight = 0
    peak_in_flight = 0
    verify_calls = 0

    def _controlled_verify(_plain: str, _hashed: str) -> bool:
        nonlocal in_flight, peak_in_flight, verify_calls
        with lock:
            in_flight += 1
            verify_calls += 1
            peak_in_flight = max(peak_in_flight, in_flight)
            if in_flight == limit:
                slots_filled.set()
        try:
            release_gate.wait(timeout=5.0)
            return True
        finally:
            with lock:
                in_flight -= 1

    monkeypatch.setattr(auth_service_mod, "verify_password", _controlled_verify)

    app = FastAPI()
    app.include_router(admin_auth_router.router)
    for exc_class, handler in get_admin_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[get_auth_service] = lambda: AuthService(ctx)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        req_tasks = [
            asyncio.create_task(
                client.post(
                    "/auth/login",
                    json={
                        "email": "admin@example.com",
                        "password": "correct-password-123",  # pragma: allowlist secret
                    },
                )
            )
            for _ in range(burst_size)
        ]
        try:
            await asyncio.to_thread(slots_filled.wait, 2.0)
            await asyncio.sleep(0.12)
            release_gate.set()
            responses = await asyncio.gather(*req_tasks)
        finally:
            release_gate.set()

        # After the burst drains, a fresh login succeeds immediately.
        followup = await client.post(
            "/auth/login",
            json={
                "email": "admin@example.com",
                "password": "correct-password-123",  # pragma: allowlist secret
            },
        )
        assert followup.status_code == 200

    ok_responses = [r for r in responses if r.status_code == 200]
    busy_responses = [r for r in responses if r.status_code == 503]

    assert len(ok_responses) == limit
    assert len(busy_responses) == burst_size - limit
    assert peak_in_flight == limit
    assert verify_calls == limit + 1  # burst winners + 1 post-burst followup

    for resp in busy_responses:
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.headers["Retry-After"] == "1"
        body = resp.json()
        assert body["type"] == "password_hasher_busy"
        assert body["status"] == 503
        assert body["instance"] == "/auth/login"


def test_local_login_submit_returns_503_with_retry_after_when_argon2_busy() -> None:
    """POST /login on the auth surface maps PasswordHasherBusyError to 503 + Retry-After."""
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        local_login=LocalLoginConfig(enabled=True),
        platform_clients=[
            PlatformClientConfig(
                client_id="platform-app", redirect_uris=["https://app.example.com/cb"]
            )
        ],
    )
    ctx.config.auth.idp.enabled = False
    ctx.config.admin.auth.jwt_secret = SecretStr("local-login-busy-test-secret")

    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    for exc_class, handler in get_auth_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()

    client = TestClient(app)
    ls = sign_payload(
        {
            "client_id": "platform-app",
            "redirect_uri": "https://app.example.com/cb",
            "code_challenge": "challenge-abc",
            "scope": "openid",
            "nonce": None,
            "original_state": "s1",
            "iat": str(int(datetime.now(UTC).timestamp())),
        },
        login_signing_key(ctx),
        purpose="login",
    )
    form_resp = client.get("/login", params={"ls": ls})
    assert form_resp.status_code == 200
    html = form_resp.text
    csrf_marker = 'name="csrf" value="'
    csrf_start = html.index(csrf_marker) + len(csrf_marker)
    csrf = html[csrf_start : html.index('"', csrf_start)]

    busy_auth = MagicMock()
    busy_auth.authenticate = AsyncMock(side_effect=PasswordHasherBusyError(retry_after_s=4))
    with patch.object(local_login, "AuthService", return_value=busy_auth):
        resp = client.post(
            "/login",
            data={
                "email": "user@example.com",
                "password": "some-password-123",  # pragma: allowlist secret
                "ls": ls,
                "csrf": csrf,
            },
            follow_redirects=False,
        )

    assert resp.status_code == 503
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["Retry-After"] == "4"
    body = resp.json()
    assert body["type"] == "password_hasher_busy"
    assert body["instance"] == "/login"


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_oauth_token_endpoint_returns_503_with_retry_after_when_argon2_busy(
    mock_token_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
) -> None:
    """POST /oauth/token maps PasswordHasherBusyError on secret verify to 503 + Retry-After."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.verify_client_secret = AsyncMock(
        side_effect=PasswordHasherBusyError(retry_after_s=5)
    )
    mock_oauth_svc_cls.return_value = mock_oauth_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    app = FastAPI()
    app.include_router(oauth.router)
    for exc_class, handler in get_auth_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    app.state.ctx = mock_ctx
    app.state.auth_state_backend = MemoryStateBackend()

    client = TestClient(app)
    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "rt_any",
            "client_id": "oc_conf_client",
            "client_secret": "jcs_secret",  # pragma: allowlist secret
        },
    )

    assert resp.status_code == 503
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["Retry-After"] == "5"
    body = resp.json()
    assert body["type"] == "password_hasher_busy"
    assert body["instance"] == "/oauth/token"
