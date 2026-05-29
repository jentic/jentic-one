#!/usr/bin/env python3
"""
Seed fake executions and jobs into the parallel jentic-mini DB so the Monitor
page has visible data. Idempotent: rows are tagged with ``seed_*`` IDs and the
script clears prior seeds before inserting.

Usage (from inside the parallel container):
    docker exec jentic-mini-parallel python /app/scripts/seed_monitor_data.py
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from pathlib import Path


DB_PATH = Path("/app/data/jentic-mini.db")

VENDORS = [
    ("api.slack.com", "POST", "/api/chat.postMessage", 200, "success"),
    ("api.slack.com", "GET", "/api/users.list", 200, "success"),
    ("api.github.com", "GET", "/repos/acme/widgets", 200, "success"),
    ("api.github.com", "GET", "/repos/acme/widgets/issues", 200, "success"),
    ("api.github.com", "POST", "/repos/acme/widgets/issues", 201, "success"),
    ("api.stripe.com", "GET", "/v1/customers", 200, "success"),
    ("api.stripe.com", "POST", "/v1/charges", 200, "success"),
    ("api.openai.com", "POST", "/v1/chat/completions", 200, "success"),
    ("api.openai.com", "POST", "/v1/embeddings", 200, "success"),
    ("api.notion.com", "POST", "/v1/pages", 200, "success"),
]

# Fake agent identities used to scatter ownership across the seeded executions.
# Empty string (= NULL agent_id at write-time) is included so the "Unattributed"
# bucket still appears, mimicking legacy unregistered-agent traffic.
FAKE_AGENTS = [
    "agent_alpha_bot",
    "agent_billing_sync",
    "agent_support_assistant",
    "agent_release_notes",
    "",  # unattributed — preserves the legacy traffic story
]

# Friendly names surfaced by the agent picker (GET /agents -> client_name) and
# by /traces/usage?group_by=agent's `label` column.
FAKE_AGENT_NAMES = {
    "agent_alpha_bot": "Alpha Bot",
    "agent_billing_sync": "Billing Sync",
    "agent_support_assistant": "Support Assistant",
    "agent_release_notes": "Release Notes",
}

FAILURES = [
    ("api.slack.com", "POST", "/api/chat.postMessage", 429, "failed", "rate_limited"),
    ("api.github.com", "GET", "/repos/acme/missing", 404, "failed", "not_found"),
    ("api.stripe.com", "POST", "/v1/charges", 402, "failed", "card_declined"),
    ("api.openai.com", "POST", "/v1/chat/completions", 500, "failed", "upstream_5xx"),
]

NOW = time.time()
WINDOW = 24 * 3600  # last 24h


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Wipe any previous seed rows (matched by id prefix).
    cur.execute("DELETE FROM executions WHERE id LIKE 'seed_%'")
    cur.execute("DELETE FROM jobs WHERE id LIKE 'seed_%'")
    deleted = cur.rowcount
    print(f"cleared previous seeds (jobs deleted={deleted})")

    # Register fake agents so the picker dropdown populates and /traces/usage
    # can resolve friendly labels. UPSERT keeps the script idempotent.
    for client_id, client_name in FAKE_AGENT_NAMES.items():
        cur.execute(
            """
            INSERT INTO agents (client_id, client_name, status, jwks_json, created_at, approved_at)
            VALUES (?, ?, 'approved', '{}', unixepoch(), unixepoch())
            ON CONFLICT(client_id) DO UPDATE SET
                client_name=excluded.client_name,
                status='approved',
                deleted_at=NULL
            """,
            (client_id, client_name),
        )
    print(f"registered agents: {list(FAKE_AGENT_NAMES.keys())}")

    rng = random.Random(42)
    inserted_exec = 0
    inserted_jobs = 0

    # Map of execution_id → (job_id, kind) so we can later link a subset of
    # executions to jobs via executions.job_id. Drives the JobBadge cross-link
    # in the Execution Log without us having to run the actual broker.
    correlated_pairs: list[tuple[str, str]] = []

    # 80 successful executions spread over the last 24h
    for host, method, path, http, status in VENDORS * 8:
        ts = NOW - rng.random() * WINDOW
        duration = int(rng.gauss(280, 80))
        if duration < 30:
            duration = 30
        agent = rng.choice(FAKE_AGENTS) or None
        exec_id = f"seed_{uuid.uuid4().hex[:12]}"
        cur.execute(
            """
            INSERT INTO executions (
                id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                inputs_hash, status, http_status, duration_ms, error,
                created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                exec_id,
                "default",
                None,
                f"{method}/{host}{path}",
                None,
                f"specs/{host}.yaml",
                None,
                status,
                http,
                duration,
                None,
                ts,
                ts + duration / 1000.0,
                agent,
            ),
        )
        inserted_exec += 1
        # Reserve roughly 1-in-6 executions for cross-linking to a future job
        # (we'll mint and back-fill these jobs after the failure loop).
        if rng.random() < 0.16:
            correlated_pairs.append((exec_id, f"{method}/{host}{path}"))

    # 20 failures
    for host, method, path, http, status, err in FAILURES * 5:
        ts = NOW - rng.random() * WINDOW
        duration = int(rng.gauss(450, 120))
        if duration < 50:
            duration = 50
        agent = rng.choice(FAKE_AGENTS) or None
        cur.execute(
            """
            INSERT INTO executions (
                id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                inputs_hash, status, http_status, duration_ms, error,
                created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"seed_{uuid.uuid4().hex[:12]}",
                "default",
                None,
                f"{method}/{host}{path}",
                None,
                f"specs/{host}.yaml",
                None,
                status,
                http,
                duration,
                err,
                ts,
                ts + duration / 1000.0,
                agent,
            ),
        )
        inserted_exec += 1

    # A handful of running jobs (active-now pill should light up). These are
    # broker-kind: a single capability / API call dispatched async.
    for host, method, path, *_ in VENDORS[:3]:
        ts = NOW - rng.random() * 60  # last minute
        agent = rng.choice(FAKE_AGENTS) or None
        cur.execute(
            """
            INSERT INTO jobs (
                id, kind, slug_or_id, toolkit_id, status, result, error,
                http_status, upstream_async, upstream_job_url, trace_id, inputs,
                callback_url, created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"seed_{uuid.uuid4().hex[:12]}",
                "broker",
                f"{method}/{host}{path}",
                "default",
                "running",
                None,
                None,
                None,
                0,
                None,
                None,
                json.dumps({"foo": "bar"}),
                None,
                ts,
                None,
                agent,
            ),
        )
        inserted_jobs += 1

    # A few completed broker jobs over the last hour — uses the canonical
    # backend status `complete` (not `succeeded`), matching how the broker
    # router writes finished rows.
    for host, method, path, *_ in VENDORS[:5]:
        ts = NOW - rng.random() * 3600
        agent = rng.choice(FAKE_AGENTS) or None
        cur.execute(
            """
            INSERT INTO jobs (
                id, kind, slug_or_id, toolkit_id, status, result, error,
                http_status, upstream_async, upstream_job_url, trace_id, inputs,
                callback_url, created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"seed_{uuid.uuid4().hex[:12]}",
                "broker",
                f"{method}/{host}{path}",
                "default",
                "complete",
                json.dumps({"ok": True}),
                None,
                200,
                0,
                None,
                None,
                json.dumps({}),
                None,
                ts,
                ts + 1.5,
                agent,
            ),
        )
        inserted_jobs += 1

    # A small set of workflow-kind jobs so the Jobs tab's "Workflows" filter
    # has something to show. Workflows use a slug (not METHOD/host/path) and
    # always point at a trace_id — that trace lives in the executions table.
    workflow_slugs = [
        ("github_release_pipeline", "GitHub Release"),
        ("billing_reconcile", "Billing Reconcile"),
        ("daily_digest", "Daily Digest"),
    ]
    for slug, _label in workflow_slugs:
        ts = NOW - rng.random() * 3600
        agent = rng.choice(FAKE_AGENTS) or None
        wf_trace_id = f"seed_wf_{uuid.uuid4().hex[:12]}"
        # The workflow trace itself — a row in executions with workflow_id set.
        cur.execute(
            """
            INSERT INTO executions (
                id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                inputs_hash, status, http_status, duration_ms, error,
                created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wf_trace_id,
                "default",
                None,
                None,
                slug,
                f"specs/{slug}.yaml",
                None,
                "success",
                200,
                1850,
                None,
                ts,
                ts + 1.85,
                agent,
            ),
        )
        inserted_exec += 1
        # The owning job — kind=workflow, status=complete, trace_id pointing
        # back at the executions row above. This gives the Jobs tab a fully
        # correlated workflow row that links into the Execution Log.
        cur.execute(
            """
            INSERT INTO jobs (
                id, kind, slug_or_id, toolkit_id, status, result, error,
                http_status, upstream_async, upstream_job_url, trace_id, inputs,
                callback_url, created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"seed_{uuid.uuid4().hex[:12]}",
                "workflow",
                slug,
                "default",
                "complete",
                json.dumps({"ok": True}),
                None,
                200,
                0,
                None,
                wf_trace_id,
                json.dumps({}),
                None,
                ts,
                ts + 1.85,
                agent,
            ),
        )
        inserted_jobs += 1

    # Cross-link some pre-existing executions to a synthetic broker job so the
    # JobBadge cross-link in the Execution Log has something to render. We
    # mint a new job per pair and back-fill `executions.job_id` to point at it.
    correlated_pairs_used = 0
    for exec_id, slug in correlated_pairs[:20]:
        job_id = f"seed_{uuid.uuid4().hex[:12]}"
        ts = NOW - rng.random() * WINDOW
        agent = rng.choice(FAKE_AGENTS) or None
        cur.execute(
            """
            INSERT INTO jobs (
                id, kind, slug_or_id, toolkit_id, status, result, error,
                http_status, upstream_async, upstream_job_url, trace_id, inputs,
                callback_url, created_at, completed_at, agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "broker",
                slug,
                "default",
                "complete",
                json.dumps({"ok": True}),
                None,
                200,
                0,
                None,
                exec_id,
                json.dumps({}),
                None,
                ts,
                ts + 0.4,
                agent,
            ),
        )
        cur.execute(
            "UPDATE executions SET job_id = ? WHERE id = ?",
            (job_id, exec_id),
        )
        inserted_jobs += 1
        correlated_pairs_used += 1

    print(f"correlated executions↔jobs: {correlated_pairs_used}")

    conn.commit()
    conn.close()
    print(f"inserted: executions={inserted_exec}, jobs={inserted_jobs}")


if __name__ == "__main__":
    main()
