"""executions.api_id column — write-time + read-time + backfill.

Migration 0007 added an `api_id` column to `executions` so the Monitor surfaces
can JOIN the catalog (`apis` table) directly instead of parsing `operation_id`
substrings at read time. This test pins three behaviours:

1. **Write time** — `write_trace(api_id=...)` persists the column.
2. **Read time JOIN** — `GET /traces` and `GET /traces/{id}` surface
   `api_id` (catalog form) plus `api_name` from the catalog when registered;
   null when unregistered.
3. **Backfill** — legacy rows with `api_id IS NULL` get filled in from
   `operations.jentic_id` whenever the upstream API has been imported. A row
   that was already populated must not be overwritten (idempotence).
"""

import sqlite3

import pytest
from src.db import DB_PATH
from src.routers.traces import write_trace


_FIXTURE_TRACE_IDS = (
    "exec_apicol_known",
    "exec_apicol_unknown",
    "exec_apicol_legacy",
    "exec_apicol_already",
)
_FIXTURE_API_IDS = ("github.com", "unregistered.example.com")
_FIXTURE_OP_ID = "op_apicol_seeded"


@pytest.fixture
def seed_apis_and_operations(admin_client):  # noqa: ARG001
    """Seed `apis` + `operations` so the JOINs and the backfill can both hit.

    Layout:
      apis(id='github.com', name='GitHub')
      operations(jentic_id='GET/api.github.com/users', api_id='github.com')
    """
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("INSERT OR IGNORE INTO apis (id, name) VALUES ('github.com', 'GitHub')")
        cx.execute(
            """INSERT OR IGNORE INTO operations
                  (id, api_id, operation_id, jentic_id, method, path)
               VALUES (?, 'github.com', 'getUsers',
                       'GET/api.github.com/users', 'GET', '/users')""",
            (_FIXTURE_OP_ID,),
        )
        cx.commit()
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM executions WHERE id IN (?, ?, ?, ?)", _FIXTURE_TRACE_IDS)
        cx.execute("DELETE FROM operations WHERE id = ?", (_FIXTURE_OP_ID,))
        cx.execute("DELETE FROM apis WHERE id IN (?, ?)", _FIXTURE_API_IDS)
        cx.commit()


@pytest.mark.asyncio
async def test_write_trace_persists_api_id(seed_apis_and_operations):  # noqa: ARG001
    """`write_trace(api_id=...)` round-trips through the executions row."""
    await write_trace(
        trace_id="exec_apicol_known",
        toolkit_id="tk_a",
        operation_id="GET/api.github.com/users",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
        api_id="github.com",
    )
    with sqlite3.connect(DB_PATH) as cx:
        row = cx.execute(
            "SELECT api_id FROM executions WHERE id = ?",
            ("exec_apicol_known",),
        ).fetchone()
    assert row is not None
    assert row[0] == "github.com"


def test_list_traces_joins_api_name_when_registered(
    admin_client,
    seed_apis_and_operations,  # noqa: ARG001
):
    """GET /traces returns api_id + api_name from the catalog join."""
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, api_id, created_at)
               VALUES ('exec_apicol_known', 'tk_a',
                       'GET/api.github.com/users', 'success', 'github.com',
                       strftime('%s','now'))"""
        )
        cx.commit()

    resp = admin_client.get("/traces?limit=500")
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["traces"]}
    row = by_id["exec_apicol_known"]
    assert row["api_id"] == "github.com"
    assert row["api_name"] == "GitHub"


def test_list_traces_renders_unknown_api_with_null_name(
    admin_client,
    seed_apis_and_operations,  # noqa: ARG001
):
    """An execution stamped with an api_id not in the catalog still surfaces
    the api_id (so the frontend can render the host) but api_name is null.
    """
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, api_id, created_at)
               VALUES ('exec_apicol_unknown', 'tk_a',
                       'GET/unregistered.example.com/x', 'success',
                       'unregistered.example.com', strftime('%s','now'))"""
        )
        cx.commit()

    resp = admin_client.get("/traces?limit=500")
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["traces"]}
    row = by_id["exec_apicol_unknown"]
    assert row["api_id"] == "unregistered.example.com"
    assert row["api_name"] is None


def test_get_trace_includes_api_id_and_name(
    admin_client,
    seed_apis_and_operations,  # noqa: ARG001
):
    """GET /traces/{id} surfaces the same join as the list endpoint."""
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, api_id, created_at)
               VALUES ('exec_apicol_known', 'tk_a',
                       'GET/api.github.com/users', 'success', 'github.com',
                       strftime('%s','now'))"""
        )
        cx.commit()

    resp = admin_client.get("/traces/exec_apicol_known")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_id"] == "github.com"
    assert body["api_name"] == "GitHub"


def test_filter_traces_by_api_id_uses_indexed_column(
    admin_client,
    seed_apis_and_operations,  # noqa: ARG001
):
    """`?api_id=X` is an exact column match, not a substring scan."""
    with sqlite3.connect(DB_PATH) as cx:
        cx.executemany(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, api_id, created_at)
               VALUES (?, 'tk_a', ?, 'success', ?, strftime('%s','now'))""",
            [
                (
                    "exec_apicol_known",
                    "GET/api.github.com/users",
                    "github.com",
                ),
                (
                    "exec_apicol_unknown",
                    "GET/unregistered.example.com/x",
                    "unregistered.example.com",
                ),
            ],
        )
        cx.commit()

    resp = admin_client.get("/traces?api_id=github.com&limit=500")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()["traces"]}
    assert "exec_apicol_known" in ids
    assert "exec_apicol_unknown" not in ids


def test_usage_group_by_api_uses_column_and_catalog_label(
    admin_client,
    seed_apis_and_operations,  # noqa: ARG001
):
    """group_by=api groups on the indexed column and joins apis.name as label."""
    with sqlite3.connect(DB_PATH) as cx:
        cx.executemany(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, duration_ms,
                   api_id, created_at)
               VALUES (?, 'tk_a', ?, 'success', 100, ?, strftime('%s','now'))""",
            [
                (
                    "exec_apicol_known",
                    "GET/api.github.com/users",
                    "github.com",
                ),
                (
                    "exec_apicol_unknown",
                    "GET/unregistered.example.com/x",
                    "unregistered.example.com",
                ),
            ],
        )
        cx.commit()

    resp = admin_client.get("/traces/usage?group_by=api")
    assert resp.status_code == 200
    rows = {r["key"]: r for r in resp.json()["top"]}
    assert rows["github.com"]["label"] == "GitHub"
    # Unregistered host carries through as a key with a null label.
    unknown_row = rows.get("unregistered.example.com")
    assert unknown_row is not None
    assert unknown_row["label"] is None


def test_backfill_recovers_api_id_from_operations_table(
    admin_client,
    seed_apis_and_operations,  # noqa: ARG001
):
    """The 0007 backfill UPDATE recovers api_id for legacy rows whose upstream
    has been imported. Idempotence: rows already populated must not be
    overwritten.
    """
    # Two rows: one legacy (api_id NULL, will be backfilled) and one already
    # populated with a sentinel value the backfill must NOT overwrite.
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, api_id, created_at)
               VALUES ('exec_apicol_legacy', 'tk_a',
                       'GET/api.github.com/users', 'success', NULL,
                       strftime('%s','now'))"""
        )
        cx.execute(
            """INSERT INTO executions
                  (id, toolkit_id, operation_id, status, api_id, created_at)
               VALUES ('exec_apicol_already', 'tk_a',
                       'GET/api.github.com/users', 'success', 'sentinel.example',
                       strftime('%s','now'))"""
        )
        cx.commit()

        # Replay the migration's backfill UPDATE.
        cx.execute(
            """UPDATE executions
                  SET api_id = (
                      SELECT o.api_id
                        FROM operations o
                       WHERE o.jentic_id = executions.operation_id
                       LIMIT 1
                  )
                WHERE api_id IS NULL
                  AND operation_id IS NOT NULL"""
        )
        cx.commit()

        rows = dict(
            cx.execute(
                "SELECT id, api_id FROM executions WHERE id IN (?, ?)",
                ("exec_apicol_legacy", "exec_apicol_already"),
            ).fetchall()
        )
    assert rows["exec_apicol_legacy"] == "github.com"
    # Idempotence: pre-populated rows are untouched even when their
    # operation_id matches an imported operation.
    assert rows["exec_apicol_already"] == "sentinel.example"
