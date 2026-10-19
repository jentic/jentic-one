"""Unit tests for execution response serialization and actor fields."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.execution_service import ExecutionService
from jentic_one.admin.services.schemas.executions import ExecutionFilter, ExecutionView
from jentic_one.admin.web.deps import get_execution_service
from jentic_one.admin.web.routers.executions import _execution_response, router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.pagination import Page
from jentic_one.shared.web.deps import resolve_identity


def _make_request() -> MagicMock:
    request = MagicMock()
    request.base_url = "http://testserver/"
    return request


def _make_execution_view(**overrides: object) -> ExecutionView:
    defaults: dict[str, object] = {
        "id": "exec_001",
        "toolkit_id": "tk_test000000000000000000",
        "trace_id": "a" * 32,
        "started_at": datetime(2026, 6, 1, tzinfo=UTC),
        "duration_ms": 150,
        "status": "completed",
        "operation_id": "getThing",
        "operation_path": "/v1/things/{id}",
        "operation_method": "GET",
        "api": None,
        "pinned_revisions": None,
        "http_status": 200,
        "error": None,
        "created_at": datetime(2026, 6, 1, tzinfo=UTC),
        "actor_id": "usr_test",
        "actor_type": "user",
    }
    defaults.update(overrides)
    return ExecutionView(**defaults)  # type: ignore[arg-type]


def test_actor_fields_present_in_response() -> None:
    view = _make_execution_view(actor_id="agt_abc", actor_type="agent")
    request = _make_request()
    resp = _execution_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["actor_id"] == "agt_abc"
    assert data["actor_type"] == "agent"


def test_actor_fields_always_present() -> None:
    view = _make_execution_view(actor_id="usr_xyz", actor_type="user")
    request = _make_request()
    resp = _execution_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["actor_id"] == "usr_xyz"
    assert data["actor_type"] == "user"


def test_response_includes_standard_fields() -> None:
    view = _make_execution_view(actor_id="usr_xyz", actor_type="user")
    request = _make_request()
    resp = _execution_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["execution_id"] == "exec_001"
    assert data["toolkit_id"] == "tk_test000000000000000000"
    assert data["status"] == "completed"
    assert data["_links"]["self"] == "http://testserver/executions/exec_001"


def test_response_includes_operation_identity_fields() -> None:
    """The flat operation trio (id + path template + method) rides the response."""
    view = _make_execution_view()
    request = _make_request()
    resp = _execution_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["operation_id"] == "getThing"
    assert data["operation_path"] == "/v1/things/{id}"
    assert data["operation_method"] == "GET"


def test_response_operation_identity_fields_null_on_legacy_rows() -> None:
    """Rows predating the operation_path/method columns serialize them as null."""
    view = _make_execution_view(operation_path=None, operation_method=None)
    request = _make_request()
    resp = _execution_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["operation_id"] == "getThing"
    assert data["operation_path"] is None
    assert data["operation_method"] is None


async def _fake_identity() -> Identity:
    return Identity(sub="usr_test", permissions=["executions:read"])


def test_list_ignores_toolkit_id_param_and_logs_it() -> None:
    """Pins #1745: ``toolkit_id`` is not a filter on ``GET /executions``.

    No filter reaches the service, and one ``removed_query_param_ignored`` INFO
    names the param and the caller but never the value.
    """
    svc = AsyncMock(spec=ExecutionService)
    svc.list_all.return_value = Page[ExecutionView](data=[], has_more=False)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = _fake_identity
    app.dependency_overrides[get_execution_service] = lambda: svc
    client = TestClient(app)

    with structlog.testing.capture_logs() as logs:
        resp = client.get("/executions", params={"toolkit_id": "tk_secretvalue"})

    assert resp.status_code == 200
    assert svc.list_all.call_args.kwargs["filter"] == ExecutionFilter()
    ignored = [log for log in logs if log["event"] == "removed_query_param_ignored"]
    assert ignored == [
        {
            "event": "removed_query_param_ignored",
            "log_level": "info",
            "param": "toolkit_id",
            "route": "/executions",
            "actor_id": "usr_test",
        }
    ]
