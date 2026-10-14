"""add held job status and held_executions table

Adds the ``held_executions`` table to the admin schema. The ``held`` job
status needs no schema change: ``jobs.status`` is a ``VARCHAR`` and the value
set is enforced by the application enum.

The partial unique index on ``(request_fingerprint) WHERE state = 'pending'``
enforces at most one pending approval per request fingerprint, so an identical
retry joins the existing hold instead of filing a duplicate.

Downgrading cancels every ``held`` job before dropping the table: a schema
without the approval tier has no way to release one, and an application
without ``JobStatus.HELD`` cannot read the status.

Revision ID: 3306fb9172f1
Revises: 6ae76df5ece5
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3306fb9172f1"  # pragma: allowlist secret
down_revision: str | None = "6ae76df5ece5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "held_executions",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("held") if pg else None,
            nullable=False,
        ),
        sa.Column("job_id", sa.String(30), nullable=False),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("credential_id", sa.String(30), nullable=False),
        sa.Column("api_vendor", sa.String(128), nullable=False),
        sa.Column("api_name", sa.String(128), nullable=False),
        sa.Column("api_version", sa.String(128), nullable=False),
        sa.Column("operation_id", sa.String(512), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("matched_rule_id", sa.String(30), nullable=True),
        sa.Column("state", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(30), nullable=True),
        sa.Column("decision_reason", sa.String(500), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("execution_id", sa.String(30), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_held_executions_agent_state", "held_executions", ["agent_id", "state"])
    op.create_index("ix_held_executions_created_at", "held_executions", ["created_at"])
    op.create_index("ix_held_executions_created_by", "held_executions", ["created_by"])
    op.create_index("ix_held_executions_job_id", "held_executions", ["job_id"])
    op.create_index("ix_held_executions_state_expires", "held_executions", ["state", "expires_at"])
    op.create_index(
        "uq_held_executions_pending_fingerprint",
        "held_executions",
        ["request_fingerprint"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
        sqlite_where=sa.text("state = 'pending'"),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE jobs SET status = 'cancelled', visible_at = NULL,"
            " error = 'approval tier removed by downgrade' WHERE status = 'held'"
        )
    )
    op.drop_index("uq_held_executions_pending_fingerprint", table_name="held_executions")
    op.drop_index("ix_held_executions_state_expires", table_name="held_executions")
    op.drop_index("ix_held_executions_job_id", table_name="held_executions")
    op.drop_index("ix_held_executions_created_by", table_name="held_executions")
    op.drop_index("ix_held_executions_created_at", table_name="held_executions")
    op.drop_index("ix_held_executions_agent_state", table_name="held_executions")
    op.drop_table("held_executions")
