"""Workflow inputs / outputs round-trip through write_trace → GET /traces/{id}.

Migration 0007 adds executions.inputs and executions.outputs as JSON-encoded
TEXT columns. The Monitor drawer renders these in dedicated panels; broker
traces leave them NULL on purpose (PII / size). Verify:

  - Workflow runs persist both fields and the reader returns them as
    decoded JSON objects (not strings, not raw blobs).
  - Broker-style writes (no inputs/outputs kwargs) leave both columns NULL
    so the drawer renders the empty-state — same behaviour as today.
  - UPSERT on async pending → success transitions does not blow away
    inputs/outputs that were stamped by the first write. The COALESCE in
    the writer is the only thing standing between us and lost data.
"""

import sqlite3

import pytest
from src.db import DB_PATH
from src.routers.traces import write_trace


_FIXTURE_TRACE_ID = "exec_inout_test1"


@pytest.fixture
def cleanup_inout_trace():
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM execution_steps WHERE execution_id = ?", (_FIXTURE_TRACE_ID,))
        cx.execute("DELETE FROM executions WHERE id = ?", (_FIXTURE_TRACE_ID,))
        cx.commit()


@pytest.mark.asyncio
async def test_workflow_inputs_outputs_round_trip(admin_client, cleanup_inout_trace):  # noqa: ARG001
    inputs = {"owner": "octocat", "repo": "hello-world"}
    outputs = {"issue_url": "https://github.com/octocat/hello-world/issues/42"}
    await write_trace(
        trace_id=_FIXTURE_TRACE_ID,
        toolkit_id="default",
        operation_id=None,
        workflow_id="createIssueFlow",
        spec_path="gh.arazzo.json",
        status="success",
        http_status=200,
        duration_ms=120,
        error=None,
        inputs=inputs,
        outputs=outputs,
    )

    resp = admin_client.get(f"/traces/{_FIXTURE_TRACE_ID}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inputs"] == inputs
    assert body["outputs"] == outputs


@pytest.mark.asyncio
async def test_broker_style_write_leaves_inputs_outputs_null(
    admin_client,
    cleanup_inout_trace,  # noqa: ARG001
):
    await write_trace(
        trace_id=_FIXTURE_TRACE_ID,
        toolkit_id="default",
        operation_id="GET/api.example.com/things",
        workflow_id=None,
        spec_path="api.example.com/openapi.json",
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
    )
    resp = admin_client.get(f"/traces/{_FIXTURE_TRACE_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["inputs"] is None
    assert body["outputs"] is None


@pytest.mark.asyncio
async def test_upsert_preserves_inputs_outputs(admin_client, cleanup_inout_trace):  # noqa: ARG001
    """Async-job lifecycle does an initial pending write, then later updates
    status to success/error. The second write doesn't have inputs/outputs in
    hand. COALESCE in write_trace must keep the original values."""
    inputs = {"q": "first"}
    outputs = {"first": True}
    await write_trace(
        trace_id=_FIXTURE_TRACE_ID,
        toolkit_id="default",
        operation_id=None,
        workflow_id="wf",
        spec_path=None,
        status="pending",
        http_status=None,
        duration_ms=None,
        error=None,
        inputs=inputs,
        outputs=outputs,
    )
    # Status update simulating job completion — no inputs/outputs supplied.
    await write_trace(
        trace_id=_FIXTURE_TRACE_ID,
        toolkit_id="default",
        operation_id=None,
        workflow_id="wf",
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=999,
        error=None,
    )
    resp = admin_client.get(f"/traces/{_FIXTURE_TRACE_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["inputs"] == inputs
    assert body["outputs"] == outputs
