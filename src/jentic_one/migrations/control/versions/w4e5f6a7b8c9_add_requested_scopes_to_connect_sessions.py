"""add requested_scopes JSONB to connect_sessions

Moves the initiator's as-requested scope list off the flow-specific
``device_authorization_credentials`` aux table and onto the flow-agnostic
``connect_sessions`` row. Lets ``get_review_data`` stop reaching into a
device-flow-specific table for information that's session-scoped, not
flow-scoped — a step toward the auth-code + MCP handlers sharing the
same read path.

Nullable + default ``'[]'::jsonb`` so existing rows don't need a backfill.

Revision ID: w4e5f6a7b8c9
Revises: v3d4e5f6a7b8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w4e5f6a7b8c9"
down_revision: str | None = "v3d4e5f6a7b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # JSONB on Postgres, plain JSON on SQLite — the migration-status check
    # (and the SQLite integration profile) applies this chain to SQLite too.
    json_type = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
    op.add_column(
        "connect_sessions",
        sa.Column(
            "requested_scopes",
            json_type,
            nullable=True,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("connect_sessions", "requested_scopes")
