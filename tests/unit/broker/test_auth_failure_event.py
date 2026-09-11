"""Unit tests for unauthorized-access-attempt event emission."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator
from jentic_one.broker.web.deps import (
    RequireExecuteAccess,
    _auth_failure_counts,
    _auth_failure_emitted,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType


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
def _clear_counters() -> None:
    """Ensure auth failure state is clean between tests."""
    _auth_failure_counts.clear()
    _auth_failure_emitted.clear()


def _create_test_app(*, threshold: int = 3) -> TestClient:
    """Build a test client that rejects scope checks (triggers auth failure tracking)."""
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

    mock_ctx = MagicMock()
    mock_ctx.config.security.auth_failure_event_threshold = threshold
    mock_ctx.admin_db.transaction = MagicMock()
    app.state.ctx = mock_ctx

    return TestClient(app)


def test_below_threshold_no_event() -> None:
    """Fewer failures than threshold should not trigger an event."""
    client = _create_test_app(threshold=5)

    with patch("jentic_one.broker.web.deps.emit_event", new_callable=AsyncMock) as mock_emit:
        for _ in range(4):
            resp = client.post("/execute", headers={"Authorization": "Bearer at_limited"})
            assert resp.status_code == 403

    mock_emit.assert_not_called()


def test_at_threshold_emits_event() -> None:
    """Reaching the threshold should trigger exactly one event emission."""
    client = _create_test_app(threshold=3)

    emitted: list[dict[str, object]] = []

    async def _fake_emit(*args: object, **kwargs: object) -> str:
        emitted.append(dict(kwargs))
        return "evt_test"

    mock_session = AsyncMock()
    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_session)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)

    mock_ctx = MagicMock()
    mock_ctx.config.security.auth_failure_event_threshold = 3
    mock_ctx.admin_db.transaction.return_value = mock_transaction

    router = APIRouter()

    @router.post("/execute")
    async def execute(request: Request, _identity: RequireExecuteAccess) -> Response:
        return Response(content="ok", status_code=200)

    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.include_router(router)

    identity = _make_identity(sub="agnt_bad_actor", permissions=["read:only"])
    mock_resolver = AsyncMock()
    mock_resolver.resolve_access_token = AsyncMock(return_value=identity)
    opaque = CachedTokenValidator(resolver=mock_resolver, cache_ttl_seconds=60.0)
    app.state.broker_token_validator = DualTokenValidator(opaque=opaque, jwt=None)
    app.state.broker_rate_limiter = None
    app.state.ctx = mock_ctx

    with patch("jentic_one.broker.web.deps.emit_event", _fake_emit):
        client = TestClient(app)
        for _ in range(5):
            resp = client.post("/execute", headers={"Authorization": "Bearer at_limited"})
            assert resp.status_code == 403

    # Give the background task a moment to complete
    loop = asyncio.new_event_loop()
    loop.run_until_complete(asyncio.sleep(0.1))
    loop.close()

    assert len(emitted) == 1
    assert emitted[0]["type"] == "security.unauthorized_access_attempt"
    assert "agnt_bad_actor" in str(emitted[0]["summary"])
    assert emitted[0]["requires_action"] is True
