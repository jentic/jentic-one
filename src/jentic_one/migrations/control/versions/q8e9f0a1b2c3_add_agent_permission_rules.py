"""add agent_permission_rules

Permission rules keyed to an agent-credential binding (theme 5, phase 0).
``agent_id`` references the admin database, so it is a plain string column
with no FK; rule cleanup on agent deletion is an application-level sweep.
``credential_id`` FKs ``credentials`` so rules cascade with credential
deletion. Column shape mirrors ``toolkit_permission_rules`` (including
``match_mode``), evaluated sequence-ordered, first-match-wins, default-deny.

Revision ID: q8e9f0a1b2c3
Revises: p7d8e9f0a1b2
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q8e9f0a1b2c3"
down_revision: str | None = "p7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "agent_permission_rules",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function, so inserts there must go through the ORM model, whose
            # Python-side default (generate_ksuid("apr")) supplies the id.
            server_default=sa.func.generate_ksuid("apr") if pg else None,
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("credential_id", sa.String(30), nullable=False),
        sa.Column("effect", sa.String(10), nullable=False),
        sa.Column(
            "methods",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("path", sa.String(1000), nullable=True),
        sa.Column(
            "match_mode",
            sa.String(10),
            server_default=sa.text("'regex'"),
            nullable=False,
        ),
        sa.Column(
            "operations",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("is_system", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("sequence", sa.Integer, nullable=False),
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
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_permission_rules_binding_seq",
        "agent_permission_rules",
        ["agent_id", "credential_id", "sequence"],
    )
    op.create_index(
        "ix_agent_permission_rules_created_at", "agent_permission_rules", ["created_at"]
    )
    op.create_index(
        "ix_agent_permission_rules_created_by", "agent_permission_rules", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_permission_rules_created_by", table_name="agent_permission_rules")
    op.drop_index("ix_agent_permission_rules_created_at", table_name="agent_permission_rules")
    op.drop_index("ix_agent_permission_rules_binding_seq", table_name="agent_permission_rules")
    op.drop_table("agent_permission_rules")
