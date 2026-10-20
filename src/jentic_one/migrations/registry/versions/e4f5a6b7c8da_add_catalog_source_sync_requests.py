"""add catalog_sources.sync_due_at and sync_forced_at

Operator and push sync requests (``POST /catalog/sources/{slug}:sync`` and
``:notify``) never sync inline: they record the request on the source's state
row and the background scanner runs it. ``sync_due_at`` is when a requested
sync may start (a push notification arriving soon after the last run waits out
the source's notify interval); ``sync_forced_at`` marks the next run as forced
past the mass-removal guard.

The downgrade drops both columns (pending requests are lost).

Revision ID: e4f5a6b7c8da
Revises: d3e4f5a6b7c9
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8da"  # pragma: allowlist secret
down_revision: str | None = "d3e4f5a6b7c9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "catalog_sources"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("sync_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("sync_forced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("sync_forced_at")
        batch.drop_column("sync_due_at")
