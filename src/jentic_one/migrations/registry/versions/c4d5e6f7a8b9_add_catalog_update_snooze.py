"""add snooze/mute columns to catalog_update_checks (C1, #925)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_update_checks",
        sa.Column("snoozed_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "catalog_update_checks",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_update_checks", "snoozed_until")
    op.drop_column("catalog_update_checks", "snoozed_digest")
