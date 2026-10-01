"""add catalog_logos table

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"  # pragma: allowlist secret
down_revision: str | None = "e8f9a0b1c2d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "catalog_logos",
        sa.Column(
            "id",
            sa.String(length=30),
            server_default=sa.text("generate_ksuid('clg')") if pg else None,
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=True),
        sa.Column("upstream_etag", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_logos_source_url",
        "catalog_logos",
        ["source_url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_logos_source_url", table_name="catalog_logos")
    op.drop_table("catalog_logos")
