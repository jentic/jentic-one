"""Trace cross-link columns: job_id and parent_trace_id.

These columns power the Monitor page Execution Log cross-link badge ("part of
job …") and the Execution Detail panel's "part of workflow X" line.

Coverage:
1. write_trace persists both new fields and roundtrips them through the public
   GET /traces and GET /traces/{id} endpoints.
2. The broker reads X-Jentic-Parent-Trace only from loopback callers — header
   from external client.host is ignored, preventing spoofed workflow parentage.

We seed via write_trace + sqlite directly because (a) the broker call path
spins up real upstream HTTP and (b) we want to assert the storage shape
independent of dispatch concerns.
"""

import sqlite3

import pytest
from src.db import DB_PATH
from src.main import app
from src.routers.traces import write_trace
from starlette.testclient import TestClient


_FIXTURE_TRACE_IDS = (
    "exec_xlink_root1",
    "exec_xlink_child",
    "exec_xlink_solo1",
)


@pytest.fixture
def cleanup_xlink_traces():
    """Strip seeded rows; tests insert via write_trace so the upsert path is exercised."""
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM executions WHERE id IN (?,?,?)", _FIXTURE_TRACE_IDS)
        cx.commit()


@pytest.mark.asyncio
async def test_write_trace_persists_job_id_and_parent_trace(admin_client, cleanup_xlink_traces):  # noqa: ARG001
    """Both new columns survive INSERT and surface in list + detail responses."""
    await write_trace(
        trace_id="exec_xlink_child",
        toolkit_id="tk_a",
        operation_id="GET/api.github.com/users/me",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
        agent_id=None,
        job_id="job_xlink_001",
        parent_trace_id="exec_xlink_root1",
    )

    list_resp = admin_client.get("/traces?limit=500")
    assert list_resp.status_code == 200
    rows = {t["id"]: t for t in list_resp.json()["traces"]}
    row = rows["exec_xlink_child"]
    assert row["job_id"] == "job_xlink_001"
    assert row["parent_trace_id"] == "exec_xlink_root1"

    detail_resp = admin_client.get("/traces/exec_xlink_child")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["job_id"] == "job_xlink_001"
    assert detail["parent_trace_id"] == "exec_xlink_root1"


@pytest.mark.asyncio
async def test_write_trace_omits_links_when_unset(admin_client, cleanup_xlink_traces):  # noqa: ARG001
    """Top-level (non-job, non-child) traces report null for both link columns."""
    await write_trace(
        trace_id="exec_xlink_solo1",
        toolkit_id="tk_a",
        operation_id="GET/api.github.com/zen",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=15,
        error=None,
    )

    detail = admin_client.get("/traces/exec_xlink_solo1").json()
    assert detail["job_id"] is None
    assert detail["parent_trace_id"] is None


@pytest.mark.asyncio
async def test_upsert_does_not_clobber_links(admin_client, cleanup_xlink_traces):  # noqa: ARG001
    """A second write_trace for the same row (e.g. pending → success) preserves
    job_id/parent_trace_id set on the original insert.

    Regression guard for the COALESCE in the ON CONFLICT clause: without it,
    an async broker call that updates the trace from "pending" to "success"
    would overwrite a previously-stamped job_id with NULL because the late
    write_trace call sites don't pass job_id.
    """
    await write_trace(
        trace_id="exec_xlink_child",
        toolkit_id="tk_a",
        operation_id="GET/api.github.com/users/me",
        workflow_id=None,
        spec_path=None,
        status="pending",
        http_status=202,
        duration_ms=None,
        error=None,
        job_id="job_xlink_001",
        parent_trace_id="exec_xlink_root1",
    )
    # Simulate a later update that does NOT supply the link columns.
    await write_trace(
        trace_id="exec_xlink_child",
        toolkit_id="tk_a",
        operation_id="GET/api.github.com/users/me",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
    )

    detail = admin_client.get("/traces/exec_xlink_child").json()
    assert detail["status"] == "success"
    assert detail["job_id"] == "job_xlink_001"
    assert detail["parent_trace_id"] == "exec_xlink_root1"


# ── X-Jentic-Parent-Trace loopback gating ──────────────────────────────────


def _broker_parent_trace_id(client_addr, agent_key: str) -> tuple[str, str | None]:
    """Fire one broker call carrying X-Jentic-Parent-Trace from `client_addr`
    and return (trace_id, stored parent_trace_id).

    We target a host with no configured credential so the broker writes a
    `policy_denied` trace and returns 403 *before* any upstream network call —
    parent_trace_id is resolved earlier (the loopback gate), so it lands on
    the stored row regardless. The trace id comes back in X-Jentic-Execution-Id.
    """
    with TestClient(app, raise_server_exceptions=False, client=client_addr) as c:
        resp = c.get(
            "/api.nonexistent-xlink.test/v1/thing",
            headers={
                "X-Jentic-API-Key": agent_key,
                "X-Jentic-Parent-Trace": "exec_xlink_forged_parent",
            },
        )
    trace_id = resp.headers["X-Jentic-Execution-Id"]
    with sqlite3.connect(DB_PATH) as cx:
        row = cx.execute(
            "SELECT parent_trace_id FROM executions WHERE id = ?", (trace_id,)
        ).fetchone()
    return trace_id, (row[0] if row else None)


@pytest.fixture
def cleanup_parent_trace_probe():
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            "DELETE FROM executions WHERE parent_trace_id = ? OR operation_id LIKE ?",
            ("exec_xlink_forged_parent", "%nonexistent-xlink.test%"),
        )
        cx.commit()


def test_parent_trace_header_ignored_from_non_loopback(
    agent_key,
    cleanup_parent_trace_probe,  # noqa: ARG001
):
    """An external (non-loopback) caller cannot forge workflow parentage.

    The broker must drop X-Jentic-Parent-Trace when request.client.host is not
    loopback. 10.0.0.5 is in a trusted subnet (so the agent key authenticates)
    but is NOT loopback, so the header must be ignored and the stored trace's
    parent_trace_id must be NULL.
    """
    _, parent = _broker_parent_trace_id(("10.0.0.5", 50001), agent_key)
    assert parent is None, "non-loopback caller forged parent_trace_id"


def test_parent_trace_header_honored_from_loopback(
    agent_key,
    cleanup_parent_trace_probe,  # noqa: ARG001
):
    """The legitimate path: the arazzo-runner subprocess connects over
    loopback, so a 127.0.0.1 caller's X-Jentic-Parent-Trace IS honored and
    written onto the child trace."""
    _, parent = _broker_parent_trace_id(("127.0.0.1", 50002), agent_key)
    assert parent == "exec_xlink_forged_parent", "loopback parent_trace_id not honored"
