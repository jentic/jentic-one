"""add superseded_revision_id to overlays

Revision ID: a2b3c4d5e6f7
Revises: f5a6b7c8d9e0
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from jentic_one.shared.db.types import GUID

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("overlays", sa.Column("superseded_revision_id", GUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("overlays", "superseded_revision_id")
