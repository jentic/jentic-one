"""add agent_credential_bindings

Direct agent-to-credential binding (theme 5, phase 0). ``credential_id``
references the control database, so it is a plain string column with no FK
(same cross-DB pattern as ``execution_records.credential_id``). An agent may
bind several credentials of the same API; uniqueness is per (agent,
credential) pair only.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "agent_credential_bindings",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function, so inserts there must go through the ORM model, whose
            # Python-side default (generate_ksuid("acb")) supplies the id.
            server_default=sa.func.generate_ksuid("acb") if pg else None,
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("credential_id", sa.String(30), nullable=False),
        # Cross-DB pointer to control's permission_rule_sets (FK-less, like
        # credential_id). NULL = the binding's inline agent_permission_rules
        # rows apply; non-NULL = the shared set's list applies (Q-04).
        sa.Column("rule_set_id", sa.String(30), nullable=True),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id", "credential_id", name="uq_agent_credential_bindings_agent_credential"
        ),
    )
    op.create_index(
        "ix_agent_credential_bindings_agent_id", "agent_credential_bindings", ["agent_id"]
    )
    op.create_index(
        "ix_agent_credential_bindings_credential_id",
        "agent_credential_bindings",
        ["credential_id"],
    )
    op.create_index(
        "ix_agent_credential_bindings_rule_set_id",
        "agent_credential_bindings",
        ["rule_set_id"],
    )
    op.create_index(
        "ix_agent_credential_bindings_created_at", "agent_credential_bindings", ["created_at"]
    )
    op.create_index(
        "ix_agent_credential_bindings_created_by", "agent_credential_bindings", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_credential_bindings_created_by", table_name="agent_credential_bindings")
    op.drop_index("ix_agent_credential_bindings_created_at", table_name="agent_credential_bindings")
    op.drop_index(
        "ix_agent_credential_bindings_rule_set_id", table_name="agent_credential_bindings"
    )
    op.drop_index(
        "ix_agent_credential_bindings_credential_id", table_name="agent_credential_bindings"
    )
    op.drop_index("ix_agent_credential_bindings_agent_id", table_name="agent_credential_bindings")
    op.drop_table("agent_credential_bindings")
