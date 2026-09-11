"""add requested_scopes JSONB to connect_sessions

Moves the initiator's as-requested scope list off the flow-specific
``device_flow_credentials`` aux table and onto the flow-agnostic
``connect_sessions`` row. Lets ``get_review_data`` stop reaching into a
device-flow-specific table for information that's session-scoped, not
flow-scoped — a step toward the auth-code + MCP handlers sharing the
same read path.

Nullable + default ``'[]'::jsonb`` so existing rows don't need a backfill.

Revision ID: r9f0a1b2c3d4
Revises: q8e9f0a1b2c3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r9f0a1b2c3d4"
down_revision: str | None = "q8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connect_sessions",
        sa.Column(
            "requested_scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("connect_sessions", "requested_scopes")
