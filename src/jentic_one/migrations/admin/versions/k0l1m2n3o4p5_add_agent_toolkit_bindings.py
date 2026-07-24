"""add agent toolkit bindings

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
Create Date: 2026-06-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k0l1m2n3o4p5"
down_revision: str | None = "j9k0l1m2n3o4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "agent_toolkit_bindings",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function so there is no server_default there. Every insert is
            # expected to go through the ORM model, whose Python-side default
            # (generate_ksuid("atb")) supplies the id. A *raw* INSERT on SQLite
            # that omits id would violate NOT NULL — seed/test code must use the
            # ORM model or pass an explicit id.
            server_default=sa.func.generate_ksuid("atb") if pg else None,
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.String(30),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("toolkit_id", sa.String(255), nullable=False),
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
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id", "toolkit_id", name="uq_agent_toolkit_bindings_agent_toolkit"
        ),
    )
    op.create_index("ix_agent_toolkit_bindings_agent_id", "agent_toolkit_bindings", ["agent_id"])
    op.create_index(
        "ix_agent_toolkit_bindings_created_at", "agent_toolkit_bindings", ["created_at"]
    )
    op.create_index(
        "ix_agent_toolkit_bindings_created_by", "agent_toolkit_bindings", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_toolkit_bindings_created_by", table_name="agent_toolkit_bindings")
    op.drop_index("ix_agent_toolkit_bindings_created_at", table_name="agent_toolkit_bindings")
    op.drop_table("agent_toolkit_bindings")
