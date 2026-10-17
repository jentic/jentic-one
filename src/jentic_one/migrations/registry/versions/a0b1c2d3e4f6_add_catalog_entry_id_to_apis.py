"""add catalog_entry_id to apis

The catalog entry (``{source_slug}/{external_id}``) an API was imported from,
next to the existing ``catalog_api_id``. Nullable: manual and inline imports have
none, and pre-existing catalog imports are backfilled by the registry's boot
reconcile rather than here (it needs the parsed catalog snapshot).

Revision ID: a0b1c2d3e4f6
Revises: f9a0b1c2d3e4
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a0b1c2d3e4f6"  # pragma: allowlist secret
down_revision: str | None = "f9a0b1c2d3e4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("apis", sa.Column("catalog_entry_id", sa.String(320), nullable=True))
    op.create_index("ix_apis_catalog_entry_id", "apis", ["catalog_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_apis_catalog_entry_id", table_name="apis")
    op.drop_column("apis", "catalog_entry_id")
