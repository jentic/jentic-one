"""backfill catalog_update_checks.catalog_entry_id from the tracked API

Data only (the schema change is the previous revision): each check row takes
the ``catalog_entry_id`` its API records. Idempotent; a row whose API records
none stays NULL until the sweep fills it in.

The downgrade deletes every check row: they are sweep scratch state, rebuilt
by the next sweep (snoozes are lost), and the old ``spec_url`` key can't be
recovered from an entry id.

Revision ID: c2d3e4f5a6b9
Revises: c2d3e4f5a6b8
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b9"  # pragma: allowlist secret
down_revision: str | None = "c2d3e4f5a6b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE catalog_update_checks SET catalog_entry_id = ("
            "SELECT apis.catalog_entry_id FROM apis "
            "WHERE apis.id = catalog_update_checks.local_api_id) "
            "WHERE catalog_entry_id IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM catalog_update_checks"))
