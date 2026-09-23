"""add requested_permission_rules JSONB to connect_sessions

Stores the initiator's as-requested permission rules on the session row so
the human owner sees them on the review page and can accept, edit, or drop
them before ``:confirm`` persists the final set onto
``agent_permission_rules``. Same pattern as ``requested_scopes`` — captured
at ``:connect`` time, session-scoped, flow-agnostic.

Non-null default ``'[]'`` so existing rows don't need a backfill.

Revision ID: x5f6a7b8c9d0
Revises: w4e5f6a7b8c9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "x5f6a7b8c9d0"
down_revision: str | None = "w4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # JSONB on Postgres, plain JSON on SQLite — the migration-status check
    # (and the SQLite integration profile) applies this chain to SQLite too.
    json_type = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
    op.add_column(
        "connect_sessions",
        sa.Column(
            "requested_permission_rules",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("connect_sessions", "requested_permission_rules")
