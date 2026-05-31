#!/usr/bin/env python3
"""
Idempotent seeder for the Workspace demo.

Replaces the placeholder `demo-*` rows from the earlier seed pass with
real Arazzo workflows copied from
`jentic-directory-api/tests/data/workflows/`, plus a few demo toolkits
linked to the existing credentials so the API tiles can surface a
toolkit count.

Designed to be re-run safely: every insert is `INSERT OR IGNORE` /
`INSERT OR REPLACE` and we explicitly `DELETE` the prior placeholder
batch before inserting the real one.

Run from the host (the SQLite file is bind-mounted into the container,
and SQLite's locking handles cross-process access):

    python jentic-mini/scripts/seed_workspace_demo.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


# ── Paths ────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
REPO = HERE.parent  # jentic-mini/
DATA_DIR = REPO / "data"
DB_PATH = DATA_DIR / "jentic-mini.db"
DEMO_DIR = DATA_DIR / "workflows" / "demo"

# Path the *backend container* sees. The container has
# `${JENTIC_HOST_PATH}/data:/app/data` mounted, so the host path
# `<repo>/data/workflows/demo/foo.arazzo.json` is `/app/data/workflows/demo/foo.arazzo.json`
# inside the container — that's what we store in the DB so
# `parse_arazzo()` resolves correctly when listing / executing.
CONTAINER_DATA_DIR = "/app/data"


def container_path_for(host_path: Path) -> str:
    rel = host_path.relative_to(DATA_DIR)
    return f"{CONTAINER_DATA_DIR}/{rel.as_posix()}"


# ── Arazzo parsing ───────────────────────────────────────────────────────


def _load_arazzo(path: Path) -> dict[str, Any]:
    raw = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError:
            print(
                f"[skip] {path.name}: PyYAML not installed; install with "
                f"`pip install pyyaml` to enable YAML workflows.",
                file=sys.stderr,
            )
            return {}
        return yaml.safe_load(raw) or {}
    return json.loads(raw)


_HOST_FROM_OPID_RE = re.compile(r"^[A-Z]+/([^/]+)")


def _derive_involved_apis(doc: dict[str, Any]) -> list[str]:
    """Best-effort vendor extraction.

    `register_arazzo()` in the backend pulls the host out of capability-id
    style operationIds (`GET/api.stripe.com/v1/charges`). Many of the
    upstream Arazzo files use plain operationIds (`PostPaymentIntents`)
    instead, so we *also* fall back to parsing `sourceDescriptions[].url`
    which encodes the vendor as `./apis/openapi/<vendor>/...`. That
    second path is what produces accurate icons on the Workspace tile.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(vendor: str) -> None:
        if vendor and vendor not in seen:
            seen.add(vendor)
            found.append(vendor)

    for wf in doc.get("workflows", []):
        for step in wf.get("steps", []):
            op = step.get("operationId") or step.get("operationPath", "")
            m = _HOST_FROM_OPID_RE.match(op or "")
            if m:
                _add(m.group(1))

    for src in doc.get("sourceDescriptions", []):
        url = src.get("url", "")
        # Match `./apis/openapi/<vendor>/<sub>/<version>/openapi.json` (or
        # `./apis/openapi/<vendor>/<sub>/<sub2>/<version>/openapi.json`).
        # The catalog id is `<vendor>` when `<sub>` is the conventional
        # `main`, otherwise `<vendor>/<sub>`. Capturing only the first
        # segment collapses every nested sub-API to the bare hostname,
        # which then doesn't resolve in the catalog (e.g.
        # `apis/openapi/hubspot.com/CRM-contacts/v3/openapi.json` has no
        # leaf `hubspot.com` in the manifest, only `hubspot.com/<sub>`).
        m = re.search(r"apis/openapi/([^/]+)/([^/]+)/", url)
        if m:
            vendor, sub = m.group(1), m.group(2)
            _add(vendor if sub == "main" else f"{vendor}/{sub}")

    return found


def _slug_from(workflow_id: str | None, fallback: str) -> str:
    base = workflow_id or fallback
    s = re.sub(r"[^a-z0-9-]", "-", base.lower()).strip("-")
    s = re.sub(r"-+", "-", s)
    return s[:80] or "workflow"


def _row_for(arazzo_path: Path) -> dict[str, Any] | None:
    doc = _load_arazzo(arazzo_path)
    workflows = doc.get("workflows") or []
    if not workflows:
        return None
    wf = workflows[0]
    info = doc.get("info", {})
    workflow_id = wf.get("workflowId") or info.get("title") or arazzo_path.stem
    slug = _slug_from(workflow_id, arazzo_path.stem)
    name = wf.get("summary") or info.get("title") or workflow_id
    description = wf.get("description") or info.get("description") or ""
    steps = wf.get("steps") or []
    input_schema = wf.get("inputs")
    involved_apis = _derive_involved_apis(doc)

    return {
        "slug": slug,
        "name": name,
        "description": description,
        "arazzo_path": container_path_for(arazzo_path),
        "input_schema": json.dumps(input_schema) if input_schema else None,
        "steps_count": len(steps),
        "involved_apis": json.dumps(involved_apis),
    }


# ── Demo toolkits ────────────────────────────────────────────────────────
#
# Match the existing credentials (jentic-mini-admin and the ADS-B one) +
# a couple of new toolkits that *would* exist if the user had wired
# things up. We don't create new credentials here — the API-tile toolkit
# count just reads the toolkit_credentials join, so empty toolkits are
# fine (they just won't surface on any API tile).
DEMO_TOOLKITS: list[dict[str, Any]] = [
    {
        "id": "tk-customer-support",
        "name": "Customer Support",
        "description": "Toolkit used by the support team — CRM lookups + Slack notifications.",
        # Stable demo api keys — never used for real auth, but the column
        # is NOT NULL UNIQUE so each row needs something.
        "api_key": "demo_tk_customer_support_key",
    },
    {
        "id": "tk-billing-ops",
        "name": "Billing Ops",
        "description": "Stripe + Zendesk reconciliation. Read-only on the production credentials.",
        "api_key": "demo_tk_billing_ops_key",
    },
    {
        "id": "tk-internal-bots",
        "name": "Internal Bots",
        "description": "Internal automations — flight watchlists, news digests, on-call paging.",
        "api_key": "demo_tk_internal_bots_key",
    },
]

# Credentials that already exist in the DB. We link both demo toolkits
# to the ADS-B credential so the ADS-B API tile can show "Used by 2
# toolkits". The Jentic admin credential goes to a single toolkit so
# `jentic-mini.local` shows "Used by 1 toolkit".
DEMO_TOOLKIT_CREDENTIAL_LINKS: list[tuple[str, str]] = [
    ("tk-internal-bots", "adsbexchange.com-ads-b-exchange"),
    ("tk-billing-ops", "adsbexchange.com-ads-b-exchange"),
    ("tk-customer-support", "jentic-mini"),
]


# ── Demo executions ──────────────────────────────────────────────────────
#
# Each entry references one of the seeded workflow slugs by index into
# the discovered Arazzo files (sorted alphabetically for stability) and
# pins how long ago it ran. We re-derive the slug list at seed time so
# this stays in sync with whatever Arazzo files are present.
DEMO_EXECUTIONS_TEMPLATE: list[dict[str, Any]] = [
    {
        "id": "exec_seed_00001",
        "ago_s": 5 * 60,
        "duration_ms": 412,
        "status": "success",
        "http_status": 200,
    },
    {
        "id": "exec_seed_00002",
        "ago_s": 32 * 60,
        "duration_ms": 1893,
        "status": "success",
        "http_status": 200,
    },
    {
        "id": "exec_seed_00003",
        "ago_s": 2 * 3600,
        "duration_ms": 5421,
        "status": "error",
        "http_status": 500,
        "error": "Slack post step timed out after 5s",
    },
    {
        "id": "exec_seed_00004",
        "ago_s": 6 * 3600,
        "duration_ms": 720,
        "status": "success",
        "http_status": 200,
    },
    {
        "id": "exec_seed_00005",
        "ago_s": 24 * 3600,
        "duration_ms": 8392,
        "status": "success",
        "http_status": 200,
    },
]


# ── Seed ─────────────────────────────────────────────────────────────────


def seed() -> None:
    if not DB_PATH.exists():
        sys.exit(f"DB not found at {DB_PATH}; bring up the dev stack first.")
    if not DEMO_DIR.exists():
        sys.exit(f"Workflow dir missing: {DEMO_DIR}")

    arazzo_files = sorted(
        list(DEMO_DIR.glob("*.arazzo.json")) + list(DEMO_DIR.glob("*.arazzo.yaml"))
    )
    rows = [r for r in (_row_for(p) for p in arazzo_files) if r]
    if not rows:
        sys.exit(f"No parseable Arazzo files in {DEMO_DIR}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    # ── Clean prior demo-* rows from the placeholder seed ────────────
    cur.execute("DELETE FROM executions WHERE id LIKE 'exec_demo%' OR id LIKE 'exec_seed_%'")
    cur.execute("DELETE FROM workflows WHERE slug LIKE 'demo-%'")

    # ── Workflows ────────────────────────────────────────────────────
    for r in rows:
        cur.execute(
            """INSERT OR REPLACE INTO workflows
               (slug, name, description, arazzo_path, input_schema, steps_count, involved_apis, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                   (SELECT created_at FROM workflows WHERE slug = ?),
                   unixepoch()
               ))""",
            (
                r["slug"],
                r["name"],
                r["description"],
                r["arazzo_path"],
                r["input_schema"],
                r["steps_count"],
                r["involved_apis"],
                r["slug"],
            ),
        )

    # ── Toolkits + credential links ──────────────────────────────────
    for tk in DEMO_TOOLKITS:
        cur.execute(
            """INSERT OR IGNORE INTO toolkits (id, name, description, api_key)
               VALUES (?, ?, ?, ?)""",
            (tk["id"], tk["name"], tk["description"], tk["api_key"]),
        )

    for tk_id, cred_id in DEMO_TOOLKIT_CREDENTIAL_LINKS:
        # `INSERT OR IGNORE` keyed on the (toolkit_id, credential_id) UNIQUE,
        # so re-running the seed doesn't create duplicate pivots.
        cur.execute(
            """INSERT OR IGNORE INTO toolkit_credentials
               (id, toolkit_id, credential_id, alias)
               VALUES (?, ?, ?, NULL)""",
            (f"tkc-{tk_id}-{cred_id}"[:64], tk_id, cred_id),
        )

    # ── Executions ───────────────────────────────────────────────────
    now = int(time.time())
    slugs = [r["slug"] for r in rows]
    for i, ex in enumerate(DEMO_EXECUTIONS_TEMPLATE):
        slug = slugs[i % len(slugs)]
        ts = now - ex["ago_s"]
        cur.execute(
            """INSERT OR REPLACE INTO executions
               (id, toolkit_id, agent_id, operation_id, workflow_id, spec_path,
                status, http_status, duration_ms, error, created_at, completed_at)
               VALUES (?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ex["id"],
                slug,
                next((r["arazzo_path"] for r in rows if r["slug"] == slug), None),
                ex["status"],
                ex["http_status"],
                ex["duration_ms"],
                ex.get("error"),
                ts,
                ts + max(1, ex["duration_ms"] // 1000),
            ),
        )

    conn.commit()

    # ── Report ───────────────────────────────────────────────────────
    print(f"Seeded {len(rows)} workflow(s):")
    for r in rows:
        apis = json.loads(r["involved_apis"])
        api_summary = ", ".join(apis) if apis else "—"
        print(f"  · {r['slug']:<55s}  steps={r['steps_count']:<2d}  apis={api_summary}")
    print()
    print(
        f"Seeded {len(DEMO_TOOLKITS)} toolkit(s) with "
        f"{len(DEMO_TOOLKIT_CREDENTIAL_LINKS)} credential link(s)."
    )
    print(f"Seeded {len(DEMO_EXECUTIONS_TEMPLATE)} execution(s).")

    conn.close()


if __name__ == "__main__":
    seed()
