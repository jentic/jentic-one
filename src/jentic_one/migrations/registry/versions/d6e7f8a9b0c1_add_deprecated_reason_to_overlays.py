"""add deprecated_reason to overlays

Revision ID: d6e7f8a9b0c1
Revises: c4d5e6f7a8b9
Create Date: 2026-08-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("overlays", sa.Column("deprecated_reason", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("overlays", "deprecated_reason")
