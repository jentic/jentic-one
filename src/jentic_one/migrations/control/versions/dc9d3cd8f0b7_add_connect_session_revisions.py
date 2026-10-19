"""add connect-session intents and revisions

A connect session can now edit an existing binding (``intent = 'edit'``) as
well as create a credential, and its ask is versioned: every session has
revision 1, each amend appends the next, and ``current_revision_id`` names
the live one. An edit snapshots the binding it started from in ``baseline``,
and holds what its approver confirmed in ``approved_change`` while a vendor
consent for added scopes is in flight.
One open edit per (agent, credential) is enforced by a partial unique index.

Schema only; ``d9305dcd765e`` backfills revision 1 for open sessions.

Revision ID: dc9d3cd8f0b7
Revises: 70ff607085b4
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dc9d3cd8f0b7"  # pragma: allowlist secret
down_revision: str | None = "70ff607085b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN_EDIT_PREDICATE = (
    "intent = 'edit' AND state IN ('created', 'awaiting_app', 'polling') AND agent_id IS NOT NULL"
)


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    # JSONB on Postgres, plain JSON on SQLite.
    json_type = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
    op.add_column(
        "connect_sessions",
        sa.Column("intent", sa.String(16), nullable=False, server_default=sa.text("'create'")),
    )
    op.add_column("connect_sessions", sa.Column("baseline", json_type, nullable=True))
    op.add_column(
        "connect_sessions", sa.Column("current_revision_id", sa.String(30), nullable=True)
    )
    op.add_column("connect_sessions", sa.Column("approved_change", json_type, nullable=True))
    op.create_index(
        "ix_connect_sessions_open_edit",
        "connect_sessions",
        ["agent_id", "credential_id"],
        unique=True,
        postgresql_where=sa.text(_OPEN_EDIT_PREDICATE),
        sqlite_where=sa.text(_OPEN_EDIT_PREDICATE),
    )
    op.create_table(
        "connect_session_revisions",
        sa.Column(
            "id",
            sa.String(30),
            primary_key=True,
            # SQLite has no ``generate_ksuid``; the ORM default fills the id.
            server_default=sa.func.generate_ksuid("csr") if pg else None,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "session_id",
            sa.String(30),
            sa.ForeignKey("connect_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("proposal", json_type, nullable=False),
        sa.Column("result", json_type, nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_connect_session_revisions_created_at", "connect_session_revisions", ["created_at"]
    )
    op.create_index(
        "ix_connect_session_revisions_created_by", "connect_session_revisions", ["created_by"]
    )
    op.create_index(
        "ix_connect_session_revisions_session_id", "connect_session_revisions", ["session_id"]
    )
    op.create_index(
        "ix_connect_session_revisions_session_id_number",
        "connect_session_revisions",
        ["session_id", "number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("connect_session_revisions")
    op.drop_index("ix_connect_sessions_open_edit", table_name="connect_sessions")
    op.drop_column("connect_sessions", "approved_change")
    op.drop_column("connect_sessions", "current_revision_id")
    op.drop_column("connect_sessions", "baseline")
    op.drop_column("connect_sessions", "intent")
