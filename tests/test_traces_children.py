"""GET /traces/{id} surfaces a `children[]` array of broker traces spawned
by the trace.

The Monitor Execution drawer renders these as a `Child broker calls` panel
with cross-links into each child's own drawer. Without this field the UI
would have to fan out one /traces/{id} per child which is wasteful and
breaks pagination on long workflows.
"""

import sqlite3
import uuid

import pytest
from src.db import DB_PATH
from src.routers.traces import write_trace


@pytest.fixture
def cleanup_children_rows():
    """Strip seeded rows after each test."""
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM executions WHERE id LIKE 'exec_ch_%'")
        cx.commit()


@pytest.mark.asyncio
async def test_workflow_trace_returns_child_broker_traces_in_order(
    admin_client,
    cleanup_children_rows,  # noqa: ARG001
):
    """Workflow trace surfaces child broker traces ordered by created_at asc."""
    parent_id = f"exec_ch_{uuid.uuid4().hex[:8]}"
    first_child = f"exec_ch_{uuid.uuid4().hex[:8]}"
    second_child = f"exec_ch_{uuid.uuid4().hex[:8]}"

    # Workflow parent.
    await write_trace(
        trace_id=parent_id,
        toolkit_id="default",
        operation_id=None,
        workflow_id="wf_review",
        spec_path="github.com/review.arazzo.json",
        status="success",
        http_status=None,
        duration_ms=900,
        error=None,
    )

    # Two child broker traces; second should sort after first.
    await write_trace(
        trace_id=first_child,
        toolkit_id="default",
        operation_id="GET/api.github.com/repos/{owner}/{repo}",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=120,
        error=None,
        parent_trace_id=parent_id,
    )
    await write_trace(
        trace_id=second_child,
        toolkit_id="default",
        operation_id="POST/api.github.com/repos/{owner}/{repo}/issues",
        workflow_id=None,
        spec_path=None,
        status="failed",
        http_status=422,
        duration_ms=80,
        error="validation_failed",
        parent_trace_id=parent_id,
    )
    # Bump created_at on the second child *after* it's inserted so the ASC
    # ordering is genuinely exercised (write_trace stamps near-identical
    # second-resolution timestamps, so without this the order is a tie).
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            "UPDATE executions SET created_at = created_at + 5 WHERE id = ?",
            (second_child,),
        )
        cx.commit()

    detail = admin_client.get(f"/traces/{parent_id}").json()
    assert detail["id"] == parent_id
    children = detail.get("children") or []
    ids_in_order = [c["id"] for c in children]
    assert ids_in_order == [first_child, second_child]

    # Per-child shape — what the panel renders.
    first = children[0]
    assert first["status"] == "success"
    assert first["http_status"] == 200
    assert first["duration_ms"] == 120
    assert first["operation_id"] == "GET/api.github.com/repos/{owner}/{repo}"


@pytest.mark.asyncio
async def test_broker_trace_has_empty_children(
    admin_client,
    cleanup_children_rows,  # noqa: ARG001
):
    """A non-workflow trace never queries for children — the field is `[]`."""
    broker_id = f"exec_ch_{uuid.uuid4().hex[:8]}"

    await write_trace(
        trace_id=broker_id,
        toolkit_id="default",
        operation_id="GET/api.example.com/anything",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
    )

    detail = admin_client.get(f"/traces/{broker_id}").json()
    assert detail["children"] == []


@pytest.mark.asyncio
async def test_workflow_trace_with_no_children_returns_empty_list(
    admin_client,
    cleanup_children_rows,  # noqa: ARG001
):
    """Workflow trace that hasn't spawned any child broker traces yet."""
    parent_id = f"exec_ch_{uuid.uuid4().hex[:8]}"

    await write_trace(
        trace_id=parent_id,
        toolkit_id="default",
        operation_id=None,
        workflow_id="wf_solo",
        spec_path="github.com/solo.arazzo.json",
        status="pending",
        http_status=None,
        duration_ms=None,
        error=None,
    )

    detail = admin_client.get(f"/traces/{parent_id}").json()
    assert detail["children"] == []
