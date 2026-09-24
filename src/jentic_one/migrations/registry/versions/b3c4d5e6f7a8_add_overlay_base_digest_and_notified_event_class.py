"""add overlay_base_digest to api_revisions and last_notified_event_class to catalog_update_checks

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_revisions",
        sa.Column("overlay_base_digest", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "catalog_update_checks",
        sa.Column("last_notified_event_class", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_update_checks", "last_notified_event_class")
    op.drop_column("api_revisions", "overlay_base_digest")
