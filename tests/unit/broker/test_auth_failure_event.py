"""Unit tests for unauthorized-access-attempt event emission."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator
from jentic_one.broker.web import deps
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventSeverity, EventType
from jentic_one.shared.state import MemoryStateBackend


def _make_identity(
    *,
    sub: str = "agnt_test1",
    permissions: list[str] | None = None,
) -> Identity:
    return Identity(
        sub=sub,
        actor_type=ActorType.AGENT,
        permissions=permissions or [],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


@pytest.fixture(autouse=True)
def _fixed_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the counting window so a test never straddles a minute boundary."""
    monkeypatch.setattr(deps, "_minute_bucket", lambda: 29_000_000)


def _make_ctx(backend: MemoryStateBackend, *, threshold: int) -> MagicMock:
    mock_session = AsyncMock()
    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_session)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    ctx = MagicMock()
    ctx.config.security.auth_failure_event_threshold = threshold
    ctx.admin_db.transaction.return_value = mock_transaction
    ctx.state_backend = backend
    return ctx


def _request(ctx: MagicMock) -> Any:
    """The slice of a ``Request`` ``_record_auth_failure`` reads."""
    identity = SimpleNamespace(actor_type=ActorType.AGENT)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)),
        state=SimpleNamespace(identity=identity),
    )


async def _drain_emits() -> None:
    await asyncio.gather(*list(deps._background_tasks))


def _create_test_app(ctx: MagicMock) -> TestClient:
    """Build a test client that rejects permission checks (triggers auth failure tracking)."""
    router = APIRouter()

    @router.post("/execute")
    async def execute(request: Request, _identity: RequireExecuteAccess) -> Response:
        return Response(content="ok", status_code=200)

    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.include_router(router)

    identity = _make_identity(permissions=["read:only"])
    mock_resolver = AsyncMock()
    mock_resolver.resolve_access_token = AsyncMock(return_value=identity)
    opaque = CachedTokenValidator(resolver=mock_resolver, cache_ttl_seconds=60.0)
    app.state.broker_token_validator = DualTokenValidator(opaque=opaque, jwt=None)
    app.state.broker_rate_limiter = None
    app.state.ctx = ctx
    return TestClient(app)


def test_permission_refusal_records_an_auth_failure() -> None:
    """Every 403 from the execute-permission gate records one auth failure."""
    client = _create_test_app(_make_ctx(MemoryStateBackend(), threshold=5))

    with patch("jentic_one.broker.web.deps._record_auth_failure", new_callable=AsyncMock) as rec:
        for _ in range(4):
            resp = client.post("/execute", headers={"Authorization": "Bearer at_limited"})
            assert resp.status_code == 403

    assert rec.await_count == 4
    assert {call.args[0] for call in rec.await_args_list} == {"agnt_test1"}


async def test_failures_count_on_the_shared_backend() -> None:
    backend = MemoryStateBackend()
    ctx = _make_ctx(backend, threshold=5)
    emit = AsyncMock(return_value="evt_test")

    with patch("jentic_one.broker.web.deps.emit_event", emit):
        for _ in range(4):
            await deps._record_auth_failure("agnt_test1", _request(ctx))

    key = f"{deps.AUTH_FAILURE_COUNT_NAMESPACE}:agnt_test1:{deps._minute_bucket()}"
    assert await backend.get(key) == b"4"
    emit.assert_not_called()


async def test_at_threshold_emits_exactly_one_event() -> None:
    emitted: list[dict[str, object]] = []

    async def _fake_emit(*args: object, **kwargs: object) -> str:
        emitted.append(dict(kwargs))
        return "evt_test"

    ctx = _make_ctx(MemoryStateBackend(), threshold=3)
    with patch("jentic_one.broker.web.deps.emit_event", _fake_emit):
        for _ in range(5):
            await deps._record_auth_failure("agnt_bad_actor", _request(ctx))
        await _drain_emits()

    assert len(emitted) == 1
    assert emitted[0]["type"] == "security.unauthorized_access_attempt"
    assert "agnt_bad_actor" in str(emitted[0]["summary"])
    assert emitted[0]["requires_action"] is True
    assert emitted[0]["actor_type"] == ActorType.AGENT.value
    # Cross-check against the documented severity matrix (issue #907).
    assert emitted[0]["severity"] == EventSeverity.WARNING
    assert emitted[0]["severity"] in EVENT_TYPE_SEVERITIES[EventType.UNAUTHORIZED_ACCESS_ATTEMPT]


async def test_two_brokers_sharing_a_backend_count_into_one_window() -> None:
    """Two broker processes on one backend reach the threshold together, emitting once.

    Each process alone stays below the threshold (2 of 3), so a per-process
    counter would emit nothing; and the emit claim is shared, so the third
    and fourth failures do not emit twice.
    """
    shared = MemoryStateBackend()
    broker_a = _make_ctx(shared, threshold=3)
    broker_b = _make_ctx(shared, threshold=3)
    emit = AsyncMock(return_value="evt_test")

    with patch("jentic_one.broker.web.deps.emit_event", emit):
        for ctx in (broker_a, broker_b, broker_a, broker_b):
            await deps._record_auth_failure("agnt_split", _request(ctx))
        await _drain_emits()

    assert emit.await_count == 1


async def test_a_refused_counter_write_is_skipped_not_raised() -> None:
    """A full ``broker-authfail`` namespace skips the count; the caller still gets its 403."""
    ctx = _make_ctx(MemoryStateBackend(max_entries=1), threshold=1)
    # Fill the namespace's one slot with another actor's counter.
    await ctx.state_backend.incr_with_ttl(f"{deps.AUTH_FAILURE_COUNT_NAMESPACE}:x:0", ttl_s=60)
    emit = AsyncMock(return_value="evt_test")

    with patch("jentic_one.broker.web.deps.emit_event", emit):
        await deps._record_auth_failure("agnt_full", _request(ctx))
        await _drain_emits()

    emit.assert_not_called()


async def test_without_a_ctx_nothing_is_counted() -> None:
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=None)
    await deps._record_auth_failure("agnt_noctx", request)
    assert not deps._background_tasks
