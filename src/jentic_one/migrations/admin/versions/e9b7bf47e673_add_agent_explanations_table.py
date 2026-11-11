"""add agent_explanations table

Adds ``agent_explanations`` to the admin schema: an agent's own account of why
it made a call, one row per subject. Today the subject is a held execution,
which the agent that filed it may explain once (``POST
/executions/holds/{id}:explain``). The unique index
``uq_agent_explanations_subject`` on ``(subject_type, subject_id)`` keeps it to
one; ``ix_agent_explanations_agent_id`` serves reads by agent.

The table starts empty. Downgrade drops it, and with it every explanation:
they are advisory text shown to reviewers, not part of any decision record.

Revision ID: e9b7bf47e673
Revises: bf1a695f4db0
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9b7bf47e673"  # pragma: allowlist secret
down_revision: str | None = "bf1a695f4db0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agent_explanations"


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("aex") if pg else None,
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(30), nullable=False),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("text", sa.String(1024), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_agent_explanations_subject", _TABLE, ["subject_type", "subject_id"], unique=True
    )
    op.create_index("ix_agent_explanations_agent_id", _TABLE, ["agent_id"])
    op.create_index("ix_agent_explanations_created_at", _TABLE, ["created_at"])
    op.create_index("ix_agent_explanations_created_by", _TABLE, ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_agent_explanations_created_by", table_name=_TABLE)
    op.drop_index("ix_agent_explanations_created_at", table_name=_TABLE)
    op.drop_index("ix_agent_explanations_agent_id", table_name=_TABLE)
    op.drop_index("uq_agent_explanations_subject", table_name=_TABLE)
    op.drop_table(_TABLE)
