"""Jobs.agent_id: stamp async jobs with the calling agent's client_id.

Mirrors the executions.agent_id column added in 0005 — async jobs need the same
tenant scoping signal so the Monitor page can filter "only this agent" without
hopping through executions.

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
    cols = {row[1] for row in bind.execute(text("PRAGMA table_info(jobs)"))}
    if "agent_id" not in cols:
        op.execute("ALTER TABLE jobs ADD COLUMN agent_id TEXT DEFAULT NULL")
    # Partial index: most pre-feature rows have agent_id IS NULL, no point
    # paying for them; mirrors the executions(agent_id, created_at) pattern.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_agent_created "
        "ON jobs(agent_id, created_at) "
        "WHERE agent_id IS NOT NULL"
    )


def downgrade() -> None:
    # SQLite cannot DROP COLUMN cleanly — leave jobs.agent_id on downgrade,
    # same convention as 0005's executions.agent_id.
    op.execute("DROP INDEX IF EXISTS idx_jobs_agent_created")
