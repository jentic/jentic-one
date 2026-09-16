"""add (state, created_at) index to connect_sessions

Serves the admin-console list endpoint (``GET /connect-sessions``): rows are
filtered by ``state`` and keyset-paginated on ``created_at`` — a composite
index covers both the filtered and unfiltered orderings.

Revision ID: z7b8c9d0e1f2
Revises: y6a7b8c9d0e1
"""

from collections.abc import Sequence

from alembic import op

revision: str = "z7b8c9d0e1f2"
down_revision: str | None = "y6a7b8c9d0e1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_connect_sessions_state_created_at",
        "connect_sessions",
        ["state", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_connect_sessions_state_created_at", table_name="connect_sessions")
