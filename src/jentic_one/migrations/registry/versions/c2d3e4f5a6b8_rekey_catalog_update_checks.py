"""rekey catalog_update_checks on the catalog entry

The update-notify sweep now probes an API through the catalog source that
lists it, so each check row records the entry it probes rather than a spec URL:

- ``spec_url`` is replaced by ``catalog_entry_id`` (``{source_slug}/{external_id}``),
  backfilled from the tracked API. Nullable: an API imported before entries were
  recorded gets its entry id from the registry's boot reconcile, and the next
  sweep fills the check row in;
- ``last_seen_etag`` is renamed ``last_seen_validator`` (whatever the source
  returns to revalidate with; for a URL source still the ETag);
- ``last_seen_content_hint`` is added: the entry's listing hint when last
  probed. An unchanged hint lets the sweep skip the fetch.

The row key (``local_api_id``) and the snooze columns are unchanged.

The downgrade deletes every check row (sweep scratch state, rebuilt by the next
sweep, which also drops snoozes) and restores the old columns.

Revision ID: c2d3e4f5a6b8
Revises: b1c2d3e4f5a7
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b8"  # pragma: allowlist secret
down_revision: str | None = "b1c2d3e4f5a7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "catalog_update_checks"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("catalog_entry_id", sa.String(320), nullable=True))
        batch.add_column(sa.Column("last_seen_content_hint", sa.Text(), nullable=True))
        batch.alter_column(
            "last_seen_etag", new_column_name="last_seen_validator", existing_type=sa.Text()
        )
    op.execute(
        sa.text(
            "UPDATE catalog_update_checks SET catalog_entry_id = ("
            "SELECT apis.catalog_entry_id FROM apis "
            "WHERE apis.id = catalog_update_checks.local_api_id)"
        )
    )
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("spec_url")
    op.create_index("ix_catalog_update_checks_catalog_entry_id", _TABLE, ["catalog_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_catalog_update_checks_catalog_entry_id", table_name=_TABLE)
    op.execute(sa.text("DELETE FROM catalog_update_checks"))
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("spec_url", sa.String(2048), nullable=False, server_default=""))
        batch.alter_column(
            "last_seen_validator", new_column_name="last_seen_etag", existing_type=sa.Text()
        )
        batch.drop_column("last_seen_content_hint")
        batch.drop_column("catalog_entry_id")
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("spec_url", server_default=None, existing_type=sa.String(2048))
