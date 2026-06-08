"""Trace listing supports query-param filtering by toolkit, agent, status, time, capability, api host.

Each test seeds a small handful of executions, calls `GET /traces` with one filter
at a time, and asserts the response set narrows correctly. The legacy no-filter
plan is unchanged — covered by `test_traces_tenant_scope.py` already.

We use the admin client because the filter behaviour is independent of tenant
scoping (which `_trace_scope_clause` covers in its own dedicated test). Mixing
both would make the assertions sensitive to fixture ordering with no extra
coverage.
"""

import sqlite3

import pytest
from src.db import DB_PATH


_FIXTURE_TRACE_IDS = (
    "exec_filter_a001",
    "exec_filter_a002",
    "exec_filter_b001",
    "exec_filter_c001",
    "exec_filter_d001",
)
_FIXTURE_AGENT_IDS = ("agnt_filter_alice", "agnt_filter_bob")


@pytest.fixture
def seeded_traces(admin_client):  # noqa: ARG001
    """Insert a fixed corpus before each test, clean up after.

    Layout:
      a001  toolkit=tk_a  agent=alice  status=success  op=GET/api.github.com/users  api_id=github.com
      a002  toolkit=tk_a  agent=alice  status=failed   wf=wf_review                  api_id=None
      b001  toolkit=tk_b  agent=bob    status=success  op=POST/api.stripe.com/charges api_id=stripe.com
      c001  toolkit=tk_a  agent=None   status=pending  op=GET/api.github.com/orgs    api_id=github.com  (1h ago)
      d001  toolkit=tk_b  agent=None   status=success  op=GET/api.openai.com/chat    api_id=openai.com  (24h ago)
    """
    now = 1_700_000_000.0  # frozen timestamp so since/until are deterministic
    rows = [
        (
            "exec_filter_a001",
            "tk_a",
            "agnt_filter_alice",
            "GET/api.github.com/users",
            None,
            "success",
            "github.com",
            now,
        ),
        (
            "exec_filter_a002",
            "tk_a",
            "agnt_filter_alice",
            None,
            "wf_review",
            "failed",
            None,
            now,
        ),
        (
            "exec_filter_b001",
            "tk_b",
            "agnt_filter_bob",
            "POST/api.stripe.com/charges",
            None,
            "success",
            "stripe.com",
            now,
        ),
        (
            "exec_filter_c001",
            "tk_a",
            None,
            "GET/api.github.com/orgs",
            None,
            "pending",
            "github.com",
            now - 3600,
        ),
        (
            "exec_filter_d001",
            "tk_b",
            None,
            "GET/api.openai.com/chat",
            None,
            "success",
            "openai.com",
            now - 86400,
        ),
    ]
    with sqlite3.connect(DB_PATH) as cx:
        cx.executemany(
            """INSERT INTO agents (client_id, client_name, jwks_json, status, created_at)
               VALUES (?, ?, '{}', 'approved', strftime('%s','now'))""",
            [
                ("agnt_filter_alice", "alice"),
                ("agnt_filter_bob", "bob"),
            ],
        )
        cx.executemany(
            """INSERT INTO executions
                  (id, toolkit_id, agent_id, operation_id, workflow_id,
                   status, api_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        cx.commit()
    yield {"now": now}
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            "DELETE FROM executions WHERE id IN (?,?,?,?,?)",
            _FIXTURE_TRACE_IDS,
        )
        cx.execute(
            "DELETE FROM agents WHERE client_id IN (?, ?)",
            _FIXTURE_AGENT_IDS,
        )
        cx.commit()


def _ids(resp):
    return {t["id"] for t in resp.json()["traces"]}


def test_filter_by_toolkit(admin_client, seeded_traces):  # noqa: ARG001
    resp = admin_client.get("/traces?toolkit_id=tk_a&limit=500")
    assert resp.status_code == 200
    assert _ids(resp) >= {"exec_filter_a001", "exec_filter_a002", "exec_filter_c001"}
    assert _ids(resp).isdisjoint({"exec_filter_b001", "exec_filter_d001"})


def test_filter_by_agent(admin_client, seeded_traces):  # noqa: ARG001
    resp = admin_client.get("/traces?agent_id=agnt_filter_alice&limit=500")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert "exec_filter_a001" in ids
    assert "exec_filter_a002" in ids
    assert ids.isdisjoint({"exec_filter_b001", "exec_filter_c001", "exec_filter_d001"})


def test_filter_by_status(admin_client, seeded_traces):  # noqa: ARG001
    resp = admin_client.get("/traces?status=failed&limit=500")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert "exec_filter_a002" in ids
    # success/pending fixture rows must not appear under status=failed
    assert ids.isdisjoint(
        {"exec_filter_a001", "exec_filter_b001", "exec_filter_c001", "exec_filter_d001"}
    )


def test_filter_by_capability_id_matches_either_column(admin_client, seeded_traces):  # noqa: ARG001
    """capability_id matches whether it lands in operation_id or workflow_id."""
    op_resp = admin_client.get("/traces?capability_id=GET/api.github.com/users&limit=500")
    assert op_resp.status_code == 200
    assert _ids(op_resp) >= {"exec_filter_a001"}

    wf_resp = admin_client.get("/traces?capability_id=wf_review&limit=500")
    assert wf_resp.status_code == 200
    assert _ids(wf_resp) >= {"exec_filter_a002"}


def test_filter_by_api_id_exact_match(admin_client, seeded_traces):  # noqa: ARG001
    """api_id matches the catalog-form `apis.id` column on executions, not a substring of operation_id."""
    resp = admin_client.get("/traces?api_id=github.com&limit=500")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert "exec_filter_a001" in ids
    assert "exec_filter_c001" in ids
    assert ids.isdisjoint({"exec_filter_b001", "exec_filter_d001"})


def test_filter_by_time_window(admin_client, seeded_traces):
    """`since` includes lower bound; `until` excludes upper bound."""
    now = seeded_traces["now"]
    # Last 30 minutes — only the rows at exactly `now`.
    resp = admin_client.get(f"/traces?since={now - 1800}&limit=500")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert {"exec_filter_a001", "exec_filter_a002", "exec_filter_b001"} <= ids
    assert ids.isdisjoint({"exec_filter_c001", "exec_filter_d001"})

    # Closed range that excludes the most recent batch.
    resp = admin_client.get(f"/traces?since={now - 7200}&until={now - 1}&limit=500")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert "exec_filter_c001" in ids
    assert ids.isdisjoint(
        {"exec_filter_a001", "exec_filter_a002", "exec_filter_b001", "exec_filter_d001"}
    )


def test_filters_compose_with_and_semantics(admin_client, seeded_traces):  # noqa: ARG001
    """Two filters narrow the result, never widen it."""
    resp = admin_client.get("/traces?toolkit_id=tk_a&status=success&limit=500")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert "exec_filter_a001" in ids
    # tk_a + success rules out a002 (failed) and c001 (pending) and b001 (tk_b).
    assert ids.isdisjoint(
        {"exec_filter_a002", "exec_filter_b001", "exec_filter_c001", "exec_filter_d001"}
    )


def test_no_filters_unchanged_legacy_plan(admin_client, seeded_traces):  # noqa: ARG001
    """The no-filter request still returns the full tenant-scoped set."""
    resp = admin_client.get("/traces?limit=500")
    assert resp.status_code == 200
    assert _ids(resp) >= set(_FIXTURE_TRACE_IDS)
