"""add catalog_update_checks table

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from jentic_one.shared.db.types import GUID

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "catalog_update_checks",
        sa.Column(
            "id",
            sa.String(length=30),
            server_default=sa.text("generate_ksuid('cuc')") if pg else None,
            nullable=False,
        ),
        sa.Column("local_api_id", GUID(), nullable=False),
        sa.Column("spec_url", sa.String(length=2048), nullable=False),
        sa.Column("last_seen_etag", sa.Text(), nullable=True),
        sa.Column("last_seen_digest", sa.String(length=64), nullable=True),
        sa.Column("last_notified_digest", sa.String(length=64), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_update_checks_local_api_id",
        "catalog_update_checks",
        ["local_api_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_update_checks_local_api_id", table_name="catalog_update_checks")
    op.drop_table("catalog_update_checks")
