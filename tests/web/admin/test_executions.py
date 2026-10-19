"""Web tests for the admin executions router."""

from __future__ import annotations

import pytest
import structlog
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_list_success(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data


def test_list_default_limit(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions")
    assert resp.status_code == 200


def test_list_limit_max_100(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions?limit=101")
    assert resp.status_code == 422


def test_list_with_terminal_status_filter(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions?status=completed")
    assert resp.status_code == 200


def test_list_ignores_toolkit_id_param(
    authed_client: TestClient, two_toolkit_executions: list[str]
) -> None:
    """Pins #1745: ``toolkit_id`` is not a filter on ``GET /executions``.

    A request carrying it gets the unfiltered list, with attribution still on
    each row, and the ignored param is logged for the operator.
    """
    with structlog.testing.capture_logs() as logs:
        resp = authed_client.get("/executions", params={"toolkit_id": "tk_alpha00000000000000000"})
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert {r["execution_id"] for r in rows} == set(two_toolkit_executions)
    # Historical attribution stays on the response.
    assert {r["toolkit_id"] for r in rows} == {
        "tk_alpha00000000000000000",
        "tk_beta000000000000000000",
    }
    ignored = [log for log in logs if log["event"] == "removed_query_param_ignored"]
    assert len(ignored) == 1 and ignored[0]["param"] == "toolkit_id"
    assert "tk_alpha00000000000000000" not in repr(ignored[0])


def test_list_rejects_non_terminal_status(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions?status=running")
    assert resp.status_code == 422


def test_list_with_api_filter(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions?api=stripe:payments:v1")
    assert resp.status_code == 200


def test_list_with_from_to_filters(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions?from=2024-01-01T00:00:00Z&to=2024-12-31T00:00:00Z")
    assert resp.status_code == 200


def test_list_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/executions")
    assert resp.status_code == 401


def test_get_not_found(authed_client: TestClient) -> None:
    resp = authed_client.get("/executions/nonexistent-exec-id")
    assert resp.status_code == 404
    assert resp.json()["type"] == "execution_not_found"
