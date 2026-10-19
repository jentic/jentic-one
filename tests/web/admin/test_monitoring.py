"""Web tests for the admin monitoring router."""

from __future__ import annotations

import pytest
import structlog
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_usage_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/monitoring/usage")
    assert resp.status_code == 401


def test_usage_ignores_toolkit_id_param(
    authed_client: TestClient, two_toolkit_executions: list[str]
) -> None:
    """Pins #1745: ``toolkit_id`` is not a filter on ``GET /monitoring/usage``.

    The stats count every execution in the window, and the ignored param is
    logged for the operator.
    """
    with structlog.testing.capture_logs() as logs:
        resp = authed_client.get(
            "/monitoring/usage", params={"toolkit_id": "tk_alpha00000000000000000"}
        )
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats["total"] == len(two_toolkit_executions)
    assert stats["success"] == len(two_toolkit_executions)
    ignored = [log for log in logs if log["event"] == "removed_query_param_ignored"]
    assert len(ignored) == 1 and ignored[0]["param"] == "toolkit_id"
    assert "tk_alpha00000000000000000" not in repr(ignored[0])
