"""Job endpoints scope reads to the calling principal's tenant.

Mirrors `test_traces_tenant_scope.py`. Seed jobs stamped with different
agents and toolkits, then verify GET /jobs and GET /jobs/{id} only return
rows for the caller. Admins see everything.
"""

import sqlite3

import aiosqlite
import pytest
from src.db import DB_PATH
from src.routers.jobs import _jobs_scope_clause  # noqa: PLC2701
from starlette.testclient import TestClient


@pytest.mark.asyncio
async def test_jobs_scope_clause_partitions_by_principal(app, client, admin_client):  # noqa: ARG001
    """Three rows: agent A's, agent B's, and one for an unrelated toolkit. Each
    principal sees only its own; admin sees all three; an unauthenticated
    caller is rejected by middleware before reaching the handler.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """INSERT INTO agents (client_id, client_name, jwks_json, status, created_at)
               VALUES (?, ?, '{}', 'approved', strftime('%s','now'))""",
            [
                ("agnt_jobs_a", "jobs-a"),
                ("agnt_jobs_b", "jobs-b"),
            ],
        )
        await db.executemany(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id,
                                 status, created_at, inputs)
               VALUES (?, 'broker', 'GET/api.example.com/x', ?, ?,
                       'pending', unixepoch(), '{}')""",
            [
                ("job_jobsA000001", "default", "agnt_jobs_a"),
                ("job_jobsB000001", "default", "agnt_jobs_b"),
                ("job_otherTK0001", "other_toolkit", None),
            ],
        )
        await db.commit()

    try:
        admin_resp = admin_client.get("/jobs?limit=100")
        assert admin_resp.status_code == 200
        admin_ids = {j["job_id"] for j in admin_resp.json()["data"]}
        assert {"job_jobsA000001", "job_jobsB000001", "job_otherTK0001"} <= admin_ids

        class _Req:
            def __init__(self, **state):
                self.state = type("S", (), state)

        # Agent A: only its own job.
        sql_a, params_a = _jobs_scope_clause(
            _Req(is_admin=False, agent_client_id="agnt_jobs_a", toolkit_id="default")
        )
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(f"SELECT id FROM jobs WHERE {sql_a}", params_a) as cur:
                rows = [r[0] for r in await cur.fetchall()]
        assert rows == ["job_jobsA000001"]

        # Agent B: only its own job.
        sql_b, params_b = _jobs_scope_clause(
            _Req(is_admin=False, agent_client_id="agnt_jobs_b", toolkit_id="default")
        )
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(f"SELECT id FROM jobs WHERE {sql_b}", params_b) as cur:
                rows = [r[0] for r in await cur.fetchall()]
        assert rows == ["job_jobsB000001"]

        # Toolkit-key caller (no agent_client_id): scoped by toolkit_id, sees
        # only the job tagged with toolkit "other_toolkit".
        sql_tk, params_tk = _jobs_scope_clause(
            _Req(is_admin=False, agent_client_id=None, toolkit_id="other_toolkit")
        )
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(f"SELECT id FROM jobs WHERE {sql_tk}", params_tk) as cur:
                rows = [r[0] for r in await cur.fetchall()]
        assert rows == ["job_otherTK0001"]

        # Admin: sees everything.
        sql_admin, params_admin = _jobs_scope_clause(_Req(is_admin=True))
        assert sql_admin == "1=1"
        assert params_admin == []

        # Fail-closed: no principal at all.
        sql_none, params_none = _jobs_scope_clause(
            _Req(is_admin=False, agent_client_id=None, toolkit_id=None)
        )
        assert sql_none == "0=1"
        assert params_none == []
    finally:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM jobs WHERE id IN (?, ?, ?)",
                ("job_jobsA000001", "job_jobsB000001", "job_otherTK0001"),
            )
            await db.execute(
                "DELETE FROM agents WHERE client_id IN (?, ?)",
                ("agnt_jobs_a", "agnt_jobs_b"),
            )
            await db.commit()


def test_jobs_endpoints_reject_anonymous_caller(app):
    """Anonymous callers never reach the jobs handlers — middleware 401s first."""
    with TestClient(app, raise_server_exceptions=False) as anon:
        assert anon.get("/jobs").status_code == 401
        assert anon.get("/jobs/job_doesnotexist").status_code == 401


def test_get_job_returns_404_for_cross_tenant(app, admin_client, agent_only_client):  # noqa: ARG001
    """Cross-tenant GET /jobs/{id} returns 404, not 403, to avoid leaking existence."""
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id,
                                 status, created_at, inputs)
               VALUES (?, 'broker', 'GET/api.example.com/x', ?, NULL,
                       'pending', unixepoch(), '{}')""",
            ("job_isolated00x1", "isolated_toolkit"),
        )
        cx.commit()
    try:
        assert admin_client.get("/jobs/job_isolated00x1").status_code == 200
        # Agent-only client is bound to toolkit "default" — gets 404, not 403.
        assert agent_only_client.get("/jobs/job_isolated00x1").status_code == 404
    finally:
        with sqlite3.connect(DB_PATH) as cx:
            cx.execute("DELETE FROM jobs WHERE id=?", ("job_isolated00x1",))
            cx.commit()


def test_list_jobs_filters_cross_tenant_rows(app, admin_client, agent_only_client):  # noqa: ARG001
    """GET /jobs returns only the caller's tenant's rows.

    Two jobs: one for the toolkit "default" (which the agent_only_client is
    bound to), one for "isolated_toolkit". The toolkit-key client must see
    only the first; admin sees both.
    """
    with sqlite3.connect(DB_PATH) as cx:
        cx.executemany(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id,
                                 status, created_at, inputs)
               VALUES (?, 'broker', 'GET/api.example.com/x', ?, NULL,
                       'pending', unixepoch(), '{}')""",
            [
                ("job_listdefault1", "default"),
                ("job_listisolate1", "isolated_toolkit"),
            ],
        )
        cx.commit()
    try:
        admin_ids = {j["job_id"] for j in admin_client.get("/jobs?limit=100").json()["data"]}
        assert {"job_listdefault1", "job_listisolate1"} <= admin_ids

        agent_ids = {j["job_id"] for j in agent_only_client.get("/jobs?limit=100").json()["data"]}
        assert "job_listdefault1" in agent_ids
        assert "job_listisolate1" not in agent_ids
    finally:
        with sqlite3.connect(DB_PATH) as cx:
            cx.execute(
                "DELETE FROM jobs WHERE id IN (?, ?)",
                ("job_listdefault1", "job_listisolate1"),
            )
            cx.commit()


def test_cancel_job_returns_404_for_cross_tenant(app, admin_client, agent_only_client):  # noqa: ARG001
    """DELETE /jobs/{id} also enforces ownership: cross-tenant returns 404
    (not 403, not 204), so a guesser can't blindly cancel another tenant's
    in-flight jobs.
    """
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id,
                                 status, created_at, inputs)
               VALUES (?, 'broker', 'GET/api.example.com/x', ?, NULL,
                       'pending', unixepoch(), '{}')""",
            ("job_cancelxxx01", "isolated_toolkit"),
        )
        cx.commit()
    try:
        assert agent_only_client.delete("/jobs/job_cancelxxx01").status_code == 404
        # Job still pending — cancel was a no-op.
        with sqlite3.connect(DB_PATH) as cx:
            row = cx.execute("SELECT status FROM jobs WHERE id=?", ("job_cancelxxx01",)).fetchone()
            assert row[0] == "pending"
    finally:
        with sqlite3.connect(DB_PATH) as cx:
            cx.execute("DELETE FROM jobs WHERE id=?", ("job_cancelxxx01",))
            cx.commit()
