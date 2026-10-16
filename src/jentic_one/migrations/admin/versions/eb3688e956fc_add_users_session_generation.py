"""add users.session_generation login-session counter (#1139)

An integer every login JWT carries as its ``sgen`` claim; the shared token
verifier refuses a user token whose claim differs from the row. It is bumped
on disable, delete, and password change/reset, so re-enabling an account does
not revive tokens issued before it was disabled. Existing rows start at ``0``
and a token without the claim reads as generation ``0``, so tokens in flight at
upgrade keep working until the account's first bump.

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


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("session_generation")
