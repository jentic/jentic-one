"""add family_issued_at to refresh_tokens

Adds a nullable ``family_issued_at`` timestamp column to ``refresh_tokens`` so
refresh-token rotation can enforce an absolute family lifetime alongside the
per-token sliding window.

Existing rows remain ``NULL`` and anchor on ``created_at`` at their first
post-upgrade rotation. No backfill or index is required.

Revision ID: c7d2e9a41b30
Revises: bf1a695f4db0
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2e9a41b30"  # pragma: allowlist secret
down_revision: str | None = "bf1a695f4db0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column("family_issued_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "family_issued_at")
