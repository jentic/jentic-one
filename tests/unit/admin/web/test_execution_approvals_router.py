"""Unit tests for the execution approvals router HTTP contract.

Pins the three endpoints (``GET /execution-approvals``,
``GET /execution-approvals/{id}``, ``POST /execution-approvals/{id}:decide``)
to their response shapes and error → HTTP status mapping.
State-machine behaviour is covered in integration tests; this file mocks
``ExecutionApprovalService`` at the boundary and exercises only the router.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.admin.services._support.pagination import Page
from jentic_one.admin.services.errors import (
    ExecutionApprovalAlreadyDecidedError,
    ExecutionApprovalForbiddenError,
    ExecutionApprovalNotFoundError,
)
from jentic_one.admin.services.execution_approval_service import ExecutionApprovalService
from jentic_one.admin.services.schemas.execution_approvals import ExecutionApprovalView
from jentic_one.admin.web.app import get_exception_handlers
from jentic_one.admin.web.deps import get_execution_approval_service
from jentic_one.admin.web.routers import execution_approvals as ea_router
from jentic_one.admin.web.routers.execution_approvals import _approval_response
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.web import deps as shared_deps

_TS = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)

_USER_IDENTITY = Identity(
    sub="usr_alice",
    permissions=["execution_approvals:read", "execution_approvals:write"],
)
_AGENT_IDENTITY = Identity(
    sub="agt_scout",
    permissions=["execution_approvals:read"],
    actor_type=ActorType.AGENT,
)


def _make_view(**overrides: Any) -> ExecutionApprovalView:
    defaults: dict[str, Any] = {
        "id": "exap_001",
        "job_id": "job_001",
        "agent_id": "agt_scout",
        "credential_id": "cred_001",
        "api_vendor": "stripe",
        "api_name": "payments",
        "api_version": "2023-01-01",
        "method": "POST",
        "path": "/v1/charges",
        "state": "pending",
        "expires_at": _TS,
        "created_at": _TS,
    }
    defaults.update(overrides)
    return ExecutionApprovalView(**defaults)


def _build_app(*, svc: Any, identity: Identity = _USER_IDENTITY) -> FastAPI:
    """Minimal FastAPI app with only the execution approvals router."""
    app = FastAPI()
    app.include_router(ea_router.router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[get_execution_approval_service] = lambda: svc
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: identity
    return app


# ---------------------------------------------------------------------------
# GET /execution-approvals
# ---------------------------------------------------------------------------


def test_list_returns_200_with_data() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.list_approvals = AsyncMock(
        return_value=Page(data=[_make_view()], has_more=False, next_cursor=None)
    )
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.get("/execution-approvals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == "exap_001"
    assert body["has_more"] is False


def test_list_passes_state_filter_to_service() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.list_approvals = AsyncMock(return_value=Page(data=[], has_more=False, next_cursor=None))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        client.get("/execution-approvals?state=pending")
    call_kwargs = svc.list_approvals.await_args.kwargs
    assert call_kwargs["state"] == "pending"


# ---------------------------------------------------------------------------
# GET /execution-approvals/{id}
# ---------------------------------------------------------------------------


def test_get_returns_200() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.get_approval = AsyncMock(return_value=_make_view())
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.get("/execution-approvals/exap_001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "exap_001"
    assert body["state"] == "pending"


def test_get_returns_404_when_not_found() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.get_approval = AsyncMock(side_effect=ExecutionApprovalNotFoundError("exap_missing"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.get("/execution-approvals/exap_missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /execution-approvals/{id}/:decide
# ---------------------------------------------------------------------------


def test_decide_approve_returns_200() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.decide = AsyncMock(return_value=_make_view(state="approved"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post(
            "/execution-approvals/exap_001/:decide",
            json={"decision": "approved"},
        )
    assert resp.status_code == 200
    assert resp.json()["state"] == "approved"


def test_decide_deny_returns_200() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.decide = AsyncMock(return_value=_make_view(state="denied"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post(
            "/execution-approvals/exap_001/:decide",
            json={"decision": "denied", "reason": "Not needed"},
        )
    assert resp.status_code == 200
    assert resp.json()["state"] == "denied"


def test_decide_returns_409_for_already_decided() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.decide = AsyncMock(side_effect=ExecutionApprovalAlreadyDecidedError("already approved"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post(
            "/execution-approvals/exap_001/:decide",
            json={"decision": "approved"},
        )
    assert resp.status_code == 409


def test_decide_returns_403_for_forbidden() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.decide = AsyncMock(side_effect=ExecutionApprovalForbiddenError("agents cannot decide"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post(
            "/execution-approvals/exap_001/:decide",
            json={"decision": "approved"},
        )
    assert resp.status_code == 403


def test_decide_passes_reason_to_service() -> None:
    svc = AsyncMock(spec=ExecutionApprovalService)
    svc.decide = AsyncMock(return_value=_make_view(state="denied"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        client.post(
            "/execution-approvals/exap_001/:decide",
            json={"decision": "denied", "reason": "Nope"},
        )
    call_kwargs = svc.decide.await_args
    assert call_kwargs is not None
    # body is the second positional arg (DecideInput)
    decide_input = call_kwargs.args[1]
    assert decide_input.reason == "Nope"
    assert decide_input.decision == "denied"


# ---------------------------------------------------------------------------
# _approval_response serialization
# ---------------------------------------------------------------------------


def test_approval_response_links_shape() -> None:
    request = MagicMock()
    request.base_url = "http://testserver/"
    view = _make_view()
    resp = _approval_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert "_links" in data
    assert "self" in data["_links"]
    assert "job" in data["_links"]


@pytest.mark.parametrize(
    "state",
    ["pending", "approved", "denied", "expired", "withdrawn"],
)
def test_approval_response_serializes_all_states(state: str) -> None:
    request = MagicMock()
    request.base_url = "http://testserver/"
    view = _make_view(state=state)
    resp = _approval_response(view, request)
    assert resp.state == state
