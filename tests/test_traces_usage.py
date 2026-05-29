"""GET /traces/usage — aggregate signals for the Monitor page."""

import sqlite3

import pytest
from src.db import DB_PATH


_FIXTURE_TRACE_IDS = (
    "exec_usage_a001",
    "exec_usage_a002",
    "exec_usage_a003",
    "exec_usage_b001",
    "exec_usage_b002",
    "exec_usage_old01",
)
_FIXTURE_AGENT_IDS = ("agnt_usage_alice", "agnt_usage_bob")


@pytest.fixture
def seeded_usage(admin_client):  # noqa: ARG001
    """Six rows: three success + one failed in tk_a, one success in tk_b, one
    success outside the time window. Latencies span 100..1000ms so percentiles
    are non-trivial.
    """
    now = 1_700_000_000.0
    rows = [
        (
            "exec_usage_a001",
            "tk_a",
            "agnt_usage_alice",
            "GET/api.github.com/users",
            "success",
            100,
            now - 60,
        ),
        (
            "exec_usage_a002",
            "tk_a",
            "agnt_usage_alice",
            "GET/api.github.com/users",
            "success",
            200,
            now - 50,
        ),
        (
            "exec_usage_a003",
            "tk_a",
            "agnt_usage_alice",
            "POST/api.github.com/issues",
            "failed",
            500,
            now - 40,
        ),
        (
            "exec_usage_b001",
            "tk_b",
            "agnt_usage_bob",
            "GET/api.stripe.com/charges",
            "success",
            300,
            now - 30,
        ),
        (
            "exec_usage_b002",
            "tk_b",
            "agnt_usage_bob",
            "GET/api.stripe.com/charges",
            "success",
            1000,
            now - 20,
        ),
        # 30 days old — outside any reasonable monitor window
        (
            "exec_usage_old01",
            "tk_a",
            None,
            "GET/api.github.com/users",
            "success",
            50,
            now - 30 * 86400,
        ),
    ]
    with sqlite3.connect(DB_PATH) as cx:
        cx.executemany(
            """INSERT INTO agents (client_id, client_name, jwks_json, status, created_at)
               VALUES (?, ?, '{}', 'approved', strftime('%s','now'))""",
            [
                ("agnt_usage_alice", "Alice"),
                ("agnt_usage_bob", "Bob"),
            ],
        )
        cx.executemany(
            """INSERT INTO executions
                  (id, toolkit_id, agent_id, operation_id, status, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        cx.commit()
    yield {"now": now}
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM executions WHERE id IN (?,?,?,?,?,?)", _FIXTURE_TRACE_IDS)
        cx.execute("DELETE FROM agents WHERE client_id IN (?,?)", _FIXTURE_AGENT_IDS)
        cx.commit()


def test_stats_counts_and_latency(admin_client, seeded_usage):
    now = seeded_usage["now"]
    resp = admin_client.get(f"/traces/usage?since={now - 3600}&until={now + 1}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    stats = body["stats"]
    # Five rows in window (the 30-day-old row is excluded).
    assert stats["total"] == 5
    assert stats["success"] == 4
    assert stats["failed"] == 1
    # Mean of (100, 200, 500, 300, 1000) = 420
    assert abs(stats["avg_ms"] - 420.0) < 0.01
    # Sorted (100, 200, 300, 500, 1000); index = floor(0.5*5)=2 → 300, 0.95*5=4 → 1000.
    assert stats["p50_ms"] == 300.0
    assert stats["p95_ms"] == 1000.0


def test_buckets_align_to_since(admin_client, seeded_usage):
    now = seeded_usage["now"]
    resp = admin_client.get(f"/traces/usage?since={now - 3600}&until={now}")
    assert resp.status_code == 200
    body = resp.json()
    # 1h window → bucket_seconds=60 (per server schedule).
    assert body["bucket_seconds"] == 60
    assert body["since"] == now - 3600
    # Each bucket ts must be a multiple of bucket_seconds offset from since.
    for b in body["buckets"]:
        offset = b["ts"] - body["since"]
        assert offset % body["bucket_seconds"] == 0
    # Bucket counts sum to the total.
    assert sum(b["total"] for b in body["buckets"]) == body["stats"]["total"]


def test_bucket_size_schedule(admin_client, seeded_usage):
    now = seeded_usage["now"]
    cases = [
        # (window_seconds, expected_bucket_seconds)
        (3600, 60),
        (86400, 3600),
        (7 * 86400, 6 * 3600),
        (30 * 86400, 86400),
    ]
    for span, expected in cases:
        resp = admin_client.get(f"/traces/usage?since={now - span}&until={now}")
        assert resp.status_code == 200, f"span={span}: {resp.text}"
        assert resp.json()["bucket_seconds"] == expected, f"span={span}"


def test_top_by_toolkit(admin_client, seeded_usage):
    now = seeded_usage["now"]
    resp = admin_client.get(f"/traces/usage?since={now - 3600}&until={now + 1}&group_by=toolkit")
    assert resp.status_code == 200
    top = resp.json()["top"]
    rows = {r["key"]: r for r in top}
    assert rows["tk_a"]["total"] == 3
    assert rows["tk_a"]["failed"] == 1
    assert rows["tk_b"]["total"] == 2
    # Ordering is by count desc.
    assert top[0]["key"] == "tk_a"


def test_top_by_agent_includes_friendly_label(admin_client, seeded_usage):
    now = seeded_usage["now"]
    resp = admin_client.get(f"/traces/usage?since={now - 3600}&until={now + 1}&group_by=agent")
    assert resp.status_code == 200
    top = resp.json()["top"]
    rows = {r["key"]: r for r in top}
    assert rows["agnt_usage_alice"]["label"] == "Alice"
    assert rows["agnt_usage_alice"]["total"] == 3
    assert rows["agnt_usage_bob"]["label"] == "Bob"


def test_top_by_api_extracts_host(admin_client, seeded_usage):
    now = seeded_usage["now"]
    resp = admin_client.get(f"/traces/usage?since={now - 3600}&until={now + 1}&group_by=api")
    assert resp.status_code == 200
    rows = {r["key"]: r for r in resp.json()["top"]}
    # api.github.com sees 3 traces (a001/a002/a003 — including the failed one).
    assert rows["api.github.com"]["total"] == 3
    assert rows["api.github.com"]["failed"] == 1
    assert rows["api.stripe.com"]["total"] == 2


def test_filter_to_one_toolkit_before_aggregating(admin_client, seeded_usage):
    now = seeded_usage["now"]
    resp = admin_client.get(f"/traces/usage?since={now - 3600}&until={now + 1}&toolkit_id=tk_a")
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats["total"] == 3
    assert stats["success"] == 2
    assert stats["failed"] == 1


def test_invalid_group_by_returns_400(admin_client, seeded_usage):  # noqa: ARG001
    resp = admin_client.get("/traces/usage?group_by=bogus")
    assert resp.status_code == 400


def test_since_must_be_less_than_until(admin_client, seeded_usage):  # noqa: ARG001
    resp = admin_client.get("/traces/usage?since=2&until=1")
    assert resp.status_code == 400


def test_default_window_is_24h(admin_client, seeded_usage):  # noqa: ARG001
    resp = admin_client.get("/traces/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["until"] - body["since"] == 86400.0
    assert body["bucket_seconds"] == 3600
