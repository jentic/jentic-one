"""Monitor link columns + indexes.

Originally added jobs.agent_id (revision 0007). Extended in the same migration
to add cross-table linkage columns used by the Monitor page:

- jobs.agent_id              - tenant scoping for async jobs (mirrors executions.agent_id)
- executions.job_id          - reverse pointer: which async job (if any) produced this trace
- executions.parent_trace_id - parent workflow trace for child broker hops
- executions.api_id          - FK-shaped pointer into apis(id) so the Monitor "API"
                               column can JOIN to the catalog without parsing
                               operation_id at read time (catalog-form id, e.g.
                               'stripe.com'). Populated at write time by the broker
                               from the credential record; legacy rows are
                               backfilled in this migration via operations.jentic_id.
- executions.inputs          - JSON-encoded workflow inputs surfaced in the drawer's
                               Inputs panel. Workflow rows only — broker rows leave
                               these NULL (request/response bodies are intentionally
                               not persisted to avoid PII leakage).
- executions.outputs         - JSON-encoded workflow outputs surfaced in the drawer's
                               Outputs panel. Same workflow-only semantics as inputs.

Plus partial indexes for the lookups the Monitor surfaces depend on:
- idx_jobs_agent_created      - "list this agent's jobs over time"
- idx_executions_parent_trace - "show all child hops of this workflow trace"
- idx_executions_api_id       - "list traces for this API" + group_by=api in /traces/usage

All ALTERs are idempotent (PRAGMA-guarded). All indexes are partial (WHERE col
IS NOT NULL) so pre-feature rows don't pay storage cost.

The api_id backfill runs once at upgrade time. It joins legacy
executions.operation_id (METHOD/host/path) against operations.jentic_id (which
the import path mints in the same shape) to recover the canonical apis.id.
The UPDATE is gated on `api_id IS NULL`, so re-running the migration is a
no-op for any row already populated by the broker writer.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-29
"""

from alembic import op
from sqlalchemy import text


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── jobs.agent_id ──────────────────────────────────────────────────────
    job_cols = {row[1] for row in bind.execute(text("PRAGMA table_info(jobs)"))}
    if "agent_id" not in job_cols:
        op.execute("ALTER TABLE jobs ADD COLUMN agent_id TEXT DEFAULT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_agent_created "
        "ON jobs(agent_id, created_at) "
        "WHERE agent_id IS NOT NULL"
    )

    # ── executions.job_id, parent_trace_id, api_id, inputs, outputs ────────
    exec_cols = {row[1] for row in bind.execute(text("PRAGMA table_info(executions)"))}
    if "job_id" not in exec_cols:
        op.execute("ALTER TABLE executions ADD COLUMN job_id TEXT DEFAULT NULL")
    if "parent_trace_id" not in exec_cols:
        op.execute("ALTER TABLE executions ADD COLUMN parent_trace_id TEXT DEFAULT NULL")
    if "api_id" not in exec_cols:
        op.execute("ALTER TABLE executions ADD COLUMN api_id TEXT DEFAULT NULL")
    # Workflow inputs / outputs surfaced in the execution drawer. JSON-encoded
    # and nullable: legacy rows keep rendering with empty input/output panels
    # exactly as they do today (the UI guards on truthiness). Broker rows
    # don't write these columns — request/response bodies stay out of the DB
    # for PII reasons; the broker drawer surfaces operation/headers from the
    # existing `operation_id` and structured columns.
    if "inputs" not in exec_cols:
        op.execute("ALTER TABLE executions ADD COLUMN inputs TEXT DEFAULT NULL")
    if "outputs" not in exec_cols:
        op.execute("ALTER TABLE executions ADD COLUMN outputs TEXT DEFAULT NULL")

    # Cross-link lookup: the trace detail drawer's "child broker calls" panel
    # queries executions WHERE parent_trace_id = ? (see src/routers/traces.py).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_parent_trace "
        "ON executions(parent_trace_id) "
        "WHERE parent_trace_id IS NOT NULL"
    )
    # Powers `/traces?api_id=X` filter and group_by=api aggregation.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_api_id "
        "ON executions(api_id) "
        "WHERE api_id IS NOT NULL"
    )

    # ── Backfill executions.api_id for legacy rows ─────────────────────────
    #
    # Both columns share the same string shape: METHOD/host/path. Imports mint
    # operations.jentic_id via _compute_jentic_id (src/routers/apis.py); the
    # broker mints executions.operation_id via the same f-string. Equality
    # join recovers the canonical apis.id whenever the upstream API has been
    # imported.
    #
    # Rows whose upstream isn't in the catalog (anonymous broker calls, custom
    # toolkits, workflow rows where operation_id IS NULL) stay NULL — the
    # read-side LEFT JOIN renders them as "Unknown" / unattributed, which
    # matches how toolkit_id and agent_id are handled today.
    op.execute(
        """
        UPDATE executions
           SET api_id = (
               SELECT o.api_id
                 FROM operations o
                WHERE o.jentic_id = executions.operation_id
                LIMIT 1
           )
         WHERE api_id IS NULL
           AND operation_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # SQLite cannot DROP COLUMN cleanly — leave the new columns in place on
    # downgrade, same convention as 0005's executions.agent_id. Drop only
    # the indexes so the schema is structurally reversible.
    op.execute("DROP INDEX IF EXISTS idx_executions_api_id")
    op.execute("DROP INDEX IF EXISTS idx_executions_parent_trace")
    op.execute("DROP INDEX IF EXISTS idx_jobs_agent_created")
