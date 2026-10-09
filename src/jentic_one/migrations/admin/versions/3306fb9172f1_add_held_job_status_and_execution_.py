"""add held job status and execution_approvals table

Adds the ``execution_approvals`` table to the admin schema. The ``held`` job
status needs no schema change: ``jobs.status`` is a ``VARCHAR`` and the value
set is enforced by the application enum.

The partial unique index on ``(request_fingerprint) WHERE state = 'pending'``
enforces at most one pending approval per request fingerprint, so an identical
retry joins the existing hold instead of filing a duplicate.

Downgrading cancels every ``held`` job before dropping the table: a schema
without the approval tier has no way to release one, and an application
without ``JobStatus.HELD`` cannot read the status.

Revision ID: 3306fb9172f1
Revises: d2e3f4a5b6c7
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3306fb9172f1"  # pragma: allowlist secret
down_revision: str | None = "d2e3f4a5b6c7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "execution_approvals",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("exap") if pg else None,
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
    op.create_index(
        "ix_execution_approvals_agent_state", "execution_approvals", ["agent_id", "state"]
    )
    op.create_index("ix_execution_approvals_created_at", "execution_approvals", ["created_at"])
    op.create_index("ix_execution_approvals_created_by", "execution_approvals", ["created_by"])
    op.create_index("ix_execution_approvals_job_id", "execution_approvals", ["job_id"])
    op.create_index(
        "ix_execution_approvals_state_expires", "execution_approvals", ["state", "expires_at"]
    )
    op.create_index(
        "uq_execution_approvals_pending_fingerprint",
        "execution_approvals",
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
    op.drop_index("uq_execution_approvals_pending_fingerprint", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_state_expires", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_job_id", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_created_by", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_created_at", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_agent_state", table_name="execution_approvals")
    op.drop_table("execution_approvals")
