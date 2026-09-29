"""rename connect_sessions.poll_token to poll_token_hash

The column holds a SHA-256 hex digest since e1a2b3c4d5f6; the name now says
so, and the unique lookup index is renamed to match. Uses
``ALTER TABLE ... RENAME COLUMN`` directly (supported by PostgreSQL and
SQLite >= 3.25) so SQLite does not need a batch table rebuild.

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2b3c4d5e6a7"  # pragma: allowlist secret
down_revision: str | None = "e1a2b3c4d5f6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_connect_sessions_poll_token", table_name="connect_sessions")
    op.execute("ALTER TABLE connect_sessions RENAME COLUMN poll_token TO poll_token_hash")
    op.create_index(
        "ix_connect_sessions_poll_token_hash",
        "connect_sessions",
        ["poll_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_connect_sessions_poll_token_hash", table_name="connect_sessions")
    op.execute("ALTER TABLE connect_sessions RENAME COLUMN poll_token_hash TO poll_token")
    op.create_index(
        "ix_connect_sessions_poll_token",
        "connect_sessions",
        ["poll_token"],
        unique=True,
    )
