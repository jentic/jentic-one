"""add users.session_generation and users.deleted_at (#1139)

``session_generation`` is an integer every login JWT carries as its ``sgen``
claim; the shared token verifier refuses a user token whose claim differs from
the row. It is bumped on disable, delete, and password change/reset, so
re-enabling an account does not revive tokens issued before it was disabled.
Existing rows start at ``0`` and a token without the claim reads as generation
``0``, so tokens in flight at upgrade keep working until the account's first
bump.

``deleted_at`` marks a deleted (terminal-but-kept) user. The admin service
refuses to enable, edit or reset the password of a row that carries it.
Rows already deleted before this revision are recognised by their tombstone
email (``deleted-<id>@local``) on an inactive row and backfilled with their
``updated_at``. A tombstoned row that was re-enabled before this revision is
still active and able to log in, so it is left unmarked rather than frozen
in an active-but-deleted state.

Revision ID: eb3688e956fc
Revises: c00de2f5d777
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eb3688e956fc"  # pragma: allowlist secret
down_revision: str | None = "c00de2f5d777"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE users SET deleted_at = updated_at"
        " WHERE deleted_at IS NULL AND active = false"
        " AND email = 'deleted-' || id || '@local'"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_column("users", "deleted_at")
        op.drop_column("users", "session_generation")
        return
    # batch_alter_table rebuilds the table and cannot preserve the
    # expression-based unique email index: drop it first, recreate it after.
    op.drop_index("ix_users_email_lower", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("session_generation")
    op.create_index("ix_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
