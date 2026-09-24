"""add catalog_api_id to apis

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The catalog identity slug (`domain[/sub-api]`) this API was imported
    # from — the only identity form where vendor and sub-API stay separable,
    # which display surfaces need for friendly titles (#910). Nullable: manual
    # and inline imports never had one, and pre-existing rows backfill lazily
    # on their next catalog re-import.
    op.add_column("apis", sa.Column("catalog_api_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("apis", "catalog_api_id")
