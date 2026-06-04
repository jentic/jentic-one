"""Workflow execution_steps writer populates the columns the drawer needs.

Until M5, the writer only stamped (step_id, http_status, output, error)
into execution_steps, leaving operation, status, and inputs empty. The
drawer renders an empty Steps section as a result. Verify that:

  - operation is resolved from the Arazzo step definition (operationId
    falls back to operationPath falls back to workflowId).
  - status is "success" when no error signal is present, "error" when
    runner_error_context or step_data["error"] is set.
  - The output column round-trips the step_data blob unchanged so the UI
    can still inspect the runner result.
  - The reader (GET /traces/{id}) exposes `operation` / `status` /
    `output` cleanly without the historical column-name swap.
"""

import sqlite3

import pytest
from src.db import DB_PATH
from src.routers.traces import write_trace


_FIXTURE_TRACE_ID = "exec_stepwriter1"


@pytest.fixture
def cleanup_step_trace():
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM execution_steps WHERE execution_id = ?", (_FIXTURE_TRACE_ID,))
        cx.execute("DELETE FROM executions WHERE id = ?", (_FIXTURE_TRACE_ID,))
        cx.commit()


@pytest.mark.asyncio
async def test_step_writer_populates_operation_and_status(admin_client, cleanup_step_trace):  # noqa: ARG001
    step_outputs = {
        "getRepo": {
            "outputs": {"name": "jentic-mini"},
        },
        "createIssue": {
            "error": "boom",
            "runner_error_context": {"http_code": 500},
        },
        "callOtherWorkflow": {
            "outputs": {"ok": True},
        },
    }
    arazzo_steps = {
        "getRepo": {"operationId": "getRepository"},
        "createIssue": {"operationPath": "{$sourceDescriptions.gh.url}#/paths/issues~1create"},
        "callOtherWorkflow": {"workflowId": "anotherWorkflow"},
        # Note: no entry for "unknownStep" — exercise the lookup miss path.
    }

    await write_trace(
        trace_id=_FIXTURE_TRACE_ID,
        toolkit_id="default",
        operation_id=None,
        workflow_id="testWorkflow",
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=100,
        error=None,
        step_outputs=step_outputs,
        arazzo_steps=arazzo_steps,
    )

    resp = admin_client.get(f"/traces/{_FIXTURE_TRACE_ID}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    steps = {s["step_id"]: s for s in body["steps"]}

    assert steps["getRepo"]["operation"] == "getRepository"
    assert steps["getRepo"]["status"] == "success"
    assert steps["getRepo"]["error"] is None
    # output round-trips the step_data blob unchanged.
    assert steps["getRepo"]["output"] == {"outputs": {"name": "jentic-mini"}}

    # Failure is detected via either `error` or runner_error_context.
    assert steps["createIssue"]["operation"].startswith("{$sourceDescriptions")
    assert steps["createIssue"]["status"] == "error"
    assert steps["createIssue"]["http_status"] == 500
    assert steps["createIssue"]["error"] == "boom"

    # workflowId fallback when operationId / operationPath are absent.
    assert steps["callOtherWorkflow"]["operation"] == "anotherWorkflow"
    assert steps["callOtherWorkflow"]["status"] == "success"


@pytest.mark.asyncio
async def test_step_writer_handles_missing_arazzo_lookup(admin_client, cleanup_step_trace):  # noqa: ARG001
    """No arazzo_steps map at all → operation stays NULL but the row still
    writes (status / output / error all populated). The pre-M5 broker path
    that called write_trace without step_outputs continues to write zero
    step rows — covered implicitly by the rest of the trace tests."""
    step_outputs = {"orphanStep": {"outputs": {"x": 1}}}
    await write_trace(
        trace_id=_FIXTURE_TRACE_ID,
        toolkit_id="default",
        operation_id=None,
        workflow_id="orphan",
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
        step_outputs=step_outputs,
        # arazzo_steps deliberately omitted.
    )
    resp = admin_client.get(f"/traces/{_FIXTURE_TRACE_ID}")
    assert resp.status_code == 200
    steps = resp.json()["steps"]
    assert len(steps) == 1
    assert steps[0]["step_id"] == "orphanStep"
    assert steps[0]["operation"] is None
    assert steps[0]["status"] == "success"
    assert steps[0]["output"] == {"outputs": {"x": 1}}
