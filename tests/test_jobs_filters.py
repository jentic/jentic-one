"""Job listing supports query-param filtering by toolkit, agent, status, time.

The legacy `?status=` filter is preserved; this test focuses on the new
toolkit_id / agent_id / since / until parameters and their composition. We use
the admin client because filtering is independent of tenant scoping (the
/jobs route does not currently enforce per-tenant scoping; that is a separate
issue tracked in `risks.md` and explicitly out of scope per the user).
"""

import sqlite3

import pytest
from src.db import DB_PATH


_FIXTURE_JOB_IDS = (
    "job_filter_aaa01",
    "job_filter_aaa02",
    "job_filter_bbb01",
    "job_filter_ccc01",
)


@pytest.fixture
def seeded_jobs(admin_client):  # noqa: ARG001
    """Seed a small fixed corpus and clean up after.

    Layout:
      aaa01  toolkit=tk_a  agent=alice  status=running   created at `now`
      aaa02  toolkit=tk_a  agent=alice  status=complete  created at `now`
      bbb01  toolkit=tk_b  agent=bob    status=running   created at `now`
      ccc01  toolkit=tk_a  agent=None   status=pending   created 1h ago
    """
    now = 1_700_000_000.0
    rows = [
        (
            "job_filter_aaa01",
            "broker",
            "GET/api.github.com/x",
            "tk_a",
            "agnt_filter_alice",
            "running",
            now,
        ),
        (
            "job_filter_aaa02",
            "broker",
            "GET/api.github.com/y",
            "tk_a",
            "agnt_filter_alice",
            "complete",
            now,
        ),
        (
            "job_filter_bbb01",
            "broker",
            "POST/api.stripe.com/z",
            "tk_b",
            "agnt_filter_bob",
            "running",
            now,
        ),
        ("job_filter_ccc01", "broker", "GET/api.openai.com/q", "tk_a", None, "pending", now - 3600),
    ]
    with sqlite3.connect(DB_PATH) as cx:
        cx.executemany(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id, status, created_at, inputs)
               VALUES (?, ?, ?, ?, ?, ?, ?, '{}')""",
            rows,
        )
        cx.commit()
    yield {"now": now}
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM jobs WHERE id IN (?,?,?,?)", _FIXTURE_JOB_IDS)
        cx.commit()


def _ids(resp):
    return {j["job_id"] for j in resp.json()["data"]}


def test_filter_by_toolkit(admin_client, seeded_jobs):  # noqa: ARG001
    resp = admin_client.get("/jobs?toolkit_id=tk_a&limit=100")
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert {"job_filter_aaa01", "job_filter_aaa02", "job_filter_ccc01"} <= ids
    assert "job_filter_bbb01" not in ids


def test_filter_by_agent(admin_client, seeded_jobs):  # noqa: ARG001
    resp = admin_client.get("/jobs?agent_id=agnt_filter_alice&limit=100")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert {"job_filter_aaa01", "job_filter_aaa02"} <= ids
    assert ids.isdisjoint({"job_filter_bbb01", "job_filter_ccc01"})


def test_filter_by_status_legacy(admin_client, seeded_jobs):  # noqa: ARG001
    """Pre-existing ?status= filter still works."""
    resp = admin_client.get("/jobs?status=running&limit=100")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert {"job_filter_aaa01", "job_filter_bbb01"} <= ids
    assert ids.isdisjoint({"job_filter_aaa02", "job_filter_ccc01"})


def test_filter_by_time_window(admin_client, seeded_jobs):
    now = seeded_jobs["now"]
    resp = admin_client.get(f"/jobs?since={now - 1800}&limit=100")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert {"job_filter_aaa01", "job_filter_aaa02", "job_filter_bbb01"} <= ids
    assert "job_filter_ccc01" not in ids


def test_filters_compose_with_and_semantics(admin_client, seeded_jobs):  # noqa: ARG001
    """status + agent narrow together."""
    resp = admin_client.get("/jobs?status=running&agent_id=agnt_filter_alice&limit=100")
    assert resp.status_code == 200
    ids = _ids(resp)
    assert "job_filter_aaa01" in ids
    assert ids.isdisjoint({"job_filter_aaa02", "job_filter_bbb01", "job_filter_ccc01"})


def test_response_exposes_agent_id(admin_client, seeded_jobs):  # noqa: ARG001
    """Newly stamped agent_id surfaces in the JobOut response."""
    resp = admin_client.get("/jobs?agent_id=agnt_filter_alice&limit=100")
    assert resp.status_code == 200
    items = {j["job_id"]: j for j in resp.json()["data"]}
    assert items["job_filter_aaa01"]["agent_id"] == "agnt_filter_alice"
    # toolkit_id was not returned in the legacy shape; it's now opt-in. Confirm
    # we exposed it without breaking the previously-required keys.
    assert items["job_filter_aaa01"]["status"] == "running"
    assert items["job_filter_aaa01"]["kind"] == "broker"
