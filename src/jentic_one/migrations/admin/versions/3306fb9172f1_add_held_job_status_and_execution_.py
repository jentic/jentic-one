"""add held job status and execution_approvals table

Adds the ``execution_approvals`` table to the admin schema. The ``held``
job status is a pure enum extension on the application layer — no schema
change is needed for it (the ``jobs.status`` column is ``VARCHAR(20)``).

The partial unique index on ``(request_fingerprint) WHERE state = 'pending'``
lets the broker join an identical retry to the existing pending hold instead
of inserting a duplicate. The SQLAlchemy UniqueConstraint on the model is
advisory; this index is the real enforcement on PostgreSQL.

Revision ID: 3306fb9172f1
Revises: 0679072d60eb
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3306fb9172f1"  # pragma: allowlist secret
down_revision: str | None = "0679072d60eb"  # pragma: allowlist secret
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
        sa.Column(
            "state",
            sa.String(16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(30), nullable=True),
        sa.Column("decision_reason", sa.String(500), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("execution_id", sa.String(30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
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

    # Partial unique index: only one pending row per fingerprint. This prevents
    # a duplicated hold when a caller retries an identical request while one is
    # already pending. Terminal rows (approved/denied/expired/withdrawn) do not
    # participate so a new request can be filed after the original is settled.
    if pg:
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_execution_approvals_pending_fingerprint "
                "ON execution_approvals (request_fingerprint) "
                "WHERE state = 'pending'"
            )
        )


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        op.execute(sa.text("DROP INDEX IF EXISTS uq_execution_approvals_pending_fingerprint"))
    op.drop_index("ix_execution_approvals_state_expires", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_job_id", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_created_by", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_created_at", table_name="execution_approvals")
    op.drop_index("ix_execution_approvals_agent_state", table_name="execution_approvals")
    op.drop_table("execution_approvals")
