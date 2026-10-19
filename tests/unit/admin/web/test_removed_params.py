"""Tests for the ignored-query-param log helper."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Scope

from jentic_one.admin.web.removed_params import log_ignored_query_params
from jentic_one.shared.auth.identity import Identity

_IDENTITY = Identity(sub="usr_test", permissions=[])


def test_logs_route_template_not_resolved_path() -> None:
    """A path-param route logs its template, so the resource id is never logged."""
    app = FastAPI()

    @app.get("/things/{thing_id}")
    async def _get_thing(thing_id: str, request: Request) -> dict[str, str]:
        log_ignored_query_params(request, _IDENTITY)
        return {"id": thing_id}

    with structlog.testing.capture_logs() as logs:
        resp = TestClient(app).get("/things/thg_concrete123", params={"toolkit_id": "tk_x"})

    assert resp.status_code == 200
    ignored = [log for log in logs if log["event"] == "removed_query_param_ignored"]
    assert ignored == [
        {
            "event": "removed_query_param_ignored",
            "log_level": "info",
            "param": "toolkit_id",
            "route": "/things/{thing_id}",
            "actor_id": "usr_test",
        }
    ]
    assert "thg_concrete123" not in repr(logs)


def test_falls_back_to_url_path_without_matched_route() -> None:
    """Outside routing there is no ``scope["route"]``; the URL path is logged."""
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/unrouted",
        "query_string": b"toolkit_id=tk_x",
        "headers": [],
    }

    with structlog.testing.capture_logs() as logs:
        log_ignored_query_params(Request(scope), _IDENTITY)

    assert [log["route"] for log in logs] == ["/unrouted"]


def test_no_log_when_no_ignored_param() -> None:
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/unrouted",
        "query_string": b"status=failed",
        "headers": [],
    }

    with structlog.testing.capture_logs() as logs:
        log_ignored_query_params(Request(scope), _IDENTITY)

    assert logs == []
