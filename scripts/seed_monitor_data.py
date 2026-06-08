#!/usr/bin/env python3
"""
Seed fake executions and jobs into the jentic-mini DB so the Monitor page has
visible data. Idempotent: rows are tagged with ``seed_*`` IDs and the script
clears prior seeds before inserting.

Usage:
    # In the running container (default DB path):
    docker exec jentic-mini python /app/scripts/seed_monitor_data.py

    # On the host, against a local DB file:
    DB_PATH=./data/jentic-mini.db python3 scripts/seed_monitor_data.py

The container path is the default; ``DB_PATH`` overrides it for host runs and
matches the env var FastAPI itself reads at startup (see src/config.py).
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
from pathlib import Path


DB_PATH = Path(os.environ.get("DB_PATH", "/app/data/jentic-mini.db"))

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

# Catalog-form api_id (matches the broker's credentials.api_id naming) plus a
# human-readable apis.name. Both are seeded into the apis table so the Monitor
# `LEFT JOIN apis` resolves a label for every seeded vendor instead of falling
# back to a raw host string.
VENDOR_CATALOG = {
    "api.slack.com": ("slack.com", "Slack"),
    "api.github.com": ("github.com", "GitHub"),
    "api.stripe.com": ("stripe.com", "Stripe"),
    "api.openai.com": ("openai.com", "OpenAI"),
    "api.notion.com": ("notion.com", "Notion"),
}

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

    # Wipe any previous seed rows (matched by id prefix). Workflow trace IDs
    # use the `seed_wf_` prefix so they're caught by `seed_%`. We also wipe
    # execution_steps here because SQLite doesn't honour ON DELETE CASCADE
    # by default (foreign_keys pragma is off on raw sqlite3 connections),
    # so step rows would otherwise leak across re-runs.
    cur.execute(
        "DELETE FROM execution_steps WHERE execution_id IN "
        "(SELECT id FROM executions WHERE id LIKE 'seed_%')"
    )
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

    # Seed catalog rows for the fake vendors so the Monitor's LEFT JOIN apis
    # resolves a human-readable label (e.g. "Slack" for slack.com). Without
    # this, group_by=api falls back to rendering the catalog id directly,
    # which is fine but loses the vendor branding in the breakdowns.
    for _host, (api_id, api_name) in VENDOR_CATALOG.items():
        cur.execute(
            """
            INSERT INTO apis (id, name, created_at)
            VALUES (?, ?, unixepoch())
            ON CONFLICT(id) DO UPDATE SET name=excluded.name
            """,
            (api_id, api_name),
        )
    print(f"registered apis: {[v[0] for v in VENDOR_CATALOG.values()]}")

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
        api_id = VENDOR_CATALOG[host][0]
        exec_id = f"seed_{uuid.uuid4().hex[:12]}"
        cur.execute(
            """
            INSERT INTO executions (
                id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                inputs_hash, status, http_status, duration_ms, error,
                created_at, completed_at, agent_id, api_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                api_id,
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
        api_id = VENDOR_CATALOG[host][0]
        cur.execute(
            """
            INSERT INTO executions (
                id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                inputs_hash, status, http_status, duration_ms, error,
                created_at, completed_at, agent_id, api_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                api_id,
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
    #
    # Each workflow gets:
    #   - realistic inputs/outputs stamped on both the trace
    #     (executions.inputs / executions.outputs) and the owning job
    #     (jobs.inputs + jobs.result.outputs) so the drawer Inputs / Outputs
    #     panels render meaningful content;
    #   - a list of per-step rows in execution_steps so the drawer's Steps
    #     panel renders something other than "0 steps". Each step mirrors
    #     what the Arazzo runner would persist: a step_id, an operation
    #     (operationId / workflowId), a status, an http_status, and a JSON
    #     `output` blob with the upstream response body or a synthetic
    #     summary. Failure cases include a non-2xx http_status and an error
    #     string so the failure-path UI has something to render too.
    workflow_fixtures = [
        {
            "slug": "github_release_pipeline",
            "label": "GitHub Release",
            "inputs": {"repo": "acme/widgets", "tag": "v1.4.0", "draft": False},
            "outputs": {
                "release_url": "https://github.com/acme/widgets/releases/tag/v1.4.0",
                "release_id": 184523991,
                "assets_uploaded": 3,
            },
            "steps": [
                {
                    "step_id": "fetchLatestCommit",
                    "operation": "GET/api.github.com/repos/{owner}/{repo}/commits/{ref}",
                    "status": "success",
                    "http_status": 200,
                    "output": {"sha": "9c2d…f1a", "message": "release: cut v1.4.0"},
                },
                {
                    "step_id": "createRelease",
                    "operation": "POST/api.github.com/repos/{owner}/{repo}/releases",
                    "status": "success",
                    "http_status": 201,
                    "output": {"id": 184523991, "tag_name": "v1.4.0"},
                },
                {
                    "step_id": "uploadAssets",
                    "operation": "POST/uploads.github.com/repos/{owner}/{repo}/releases/{id}/assets",
                    "status": "success",
                    "http_status": 201,
                    "output": {"assets_uploaded": 3},
                },
            ],
        },
        {
            "slug": "billing_reconcile",
            "label": "Billing Reconcile",
            "inputs": {"period": "2026-05", "tenant": "acme"},
            "outputs": {"invoices_processed": 142, "discrepancies": 0, "total_usd": 18420.55},
            "steps": [
                {
                    "step_id": "listInvoices",
                    "operation": "GET/api.stripe.com/v1/invoices",
                    "status": "success",
                    "http_status": 200,
                    "output": {"count": 142, "has_more": False},
                },
                {
                    "step_id": "checkBalances",
                    "operation": "GET/api.stripe.com/v1/balance",
                    "status": "success",
                    "http_status": 200,
                    "output": {"available": [{"amount": 1842055, "currency": "usd"}]},
                },
                {
                    "step_id": "writeLedger",
                    "operation": "POST/api.notion.com/v1/pages",
                    "status": "success",
                    "http_status": 200,
                    "output": {"page_id": "p_2c8f…"},
                },
            ],
        },
        {
            "slug": "daily_digest",
            "label": "Daily Digest",
            "inputs": {"channel": "#eng-updates", "since": "2026-06-03T00:00:00Z"},
            "outputs": {
                "messages_summarized": 87,
                "digest_url": "https://notion.so/acme/digest-2026-06-04",
            },
            "steps": [
                {
                    "step_id": "fetchMessages",
                    "operation": "GET/api.slack.com/api/conversations.history",
                    "status": "success",
                    "http_status": 200,
                    "output": {"messages": 87, "has_more": False},
                },
                {
                    "step_id": "summarize",
                    "operation": "POST/api.openai.com/v1/chat/completions",
                    "status": "success",
                    "http_status": 200,
                    "output": {"model": "gpt-4o-mini", "tokens": 1843},
                },
                {
                    "step_id": "publishDigest",
                    "operation": "POST/api.notion.com/v1/pages",
                    "status": "error",
                    "http_status": 429,
                    "output": None,
                    "error": "rate_limited: retry after 30s",
                },
            ],
        },
    ]
    for fixture in workflow_fixtures:
        slug = fixture["slug"]
        wf_inputs = fixture["inputs"]
        wf_outputs = fixture["outputs"]
        wf_steps = fixture["steps"]
        ts = NOW - rng.random() * 3600
        agent = rng.choice(FAKE_AGENTS) or None
        wf_trace_id = f"seed_wf_{uuid.uuid4().hex[:12]}"
        # Workflow status mirrors the worst step — if any step errored, the
        # workflow itself failed. Keeps the Steps panel and the top-level
        # status badge consistent in the seeded data.
        wf_status = "failed" if any(s["status"] == "error" for s in wf_steps) else "success"
        wf_http = 502 if wf_status == "failed" else 200
        # The workflow trace itself — a row in executions with workflow_id set.
        cur.execute(
            """
            INSERT INTO executions (
                id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                inputs_hash, status, http_status, duration_ms, error,
                created_at, completed_at, agent_id, inputs, outputs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wf_trace_id,
                "default",
                None,
                None,
                slug,
                f"specs/{slug}.yaml",
                None,
                wf_status,
                wf_http,
                1850,
                None,
                ts,
                ts + 1.85,
                agent,
                json.dumps(wf_inputs),
                json.dumps(wf_outputs),
            ),
        )
        inserted_exec += 1
        # Per-step rows so the drawer's Steps panel has content. Started_at
        # is staggered inside the workflow's window so the natural ordering
        # by started_at matches the array order of `wf_steps`.
        step_window = 1.85 / max(len(wf_steps), 1)
        for step_idx, step in enumerate(wf_steps):
            step_started = ts + step_idx * step_window
            cur.execute(
                """
                INSERT INTO execution_steps (
                    id, execution_id, step_id, operation, status, http_status,
                    inputs, output, error, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"step_{uuid.uuid4().hex[:12]}",
                    wf_trace_id,
                    step["step_id"],
                    step["operation"],
                    step["status"],
                    step["http_status"],
                    None,  # per-step inputs not modelled by the runner today
                    json.dumps(step["output"]) if step["output"] is not None else None,
                    step.get("error"),
                    step_started,
                    step_started + step_window,
                ),
            )
            # Each workflow step is fulfilled by a real broker hop, so mint a
            # *child* broker trace whose parent_trace_id points back at the
            # workflow. This is what powers the "Child broker calls" panel in
            # the workflow drawer and the children[] array on GET /traces/{id}.
            # Without these rows the panel renders empty in the seeded DB.
            child_op = step["operation"]  # e.g. "GET/api.github.com/repos/..."
            child_host = child_op.split("/", 2)[1] if "/" in child_op else None
            child_api_id = VENDOR_CATALOG.get(child_host, (None,))[0]
            cur.execute(
                """
                INSERT INTO executions (
                    id, toolkit_id, api_key_id, operation_id, workflow_id, spec_path,
                    inputs_hash, status, http_status, duration_ms, error,
                    created_at, completed_at, agent_id, api_id, parent_trace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"seed_{uuid.uuid4().hex[:12]}",
                    "default",
                    None,
                    child_op,
                    None,
                    f"specs/{child_host}.yaml" if child_host else None,
                    None,
                    "success" if step["status"] != "error" else "failed",
                    step["http_status"],
                    int(step_window * 1000),
                    step.get("error"),
                    step_started,
                    step_started + step_window,
                    agent,
                    child_api_id,
                    wf_trace_id,
                ),
            )
            inserted_exec += 1
        # executions row above. Status mirrors the trace status so the Jobs
        # tab and the Execution Log agree on whether this workflow failed.
        job_status = "error" if wf_status == "failed" else "complete"
        job_error = (
            "step publishDigest: rate_limited: retry after 30s" if wf_status == "failed" else None
        )
        job_result = (
            json.dumps({"ok": True, "outputs": wf_outputs}) if wf_status == "success" else None
        )
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
                job_status,
                job_result,
                job_error,
                wf_http,
                0,
                None,
                wf_trace_id,
                json.dumps(wf_inputs),
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
