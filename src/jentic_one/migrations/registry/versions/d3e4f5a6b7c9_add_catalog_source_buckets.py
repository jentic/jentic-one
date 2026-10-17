"""add catalog_source_buckets

The shared rate bucket of a catalog source configured with
``requests_per_second``: one row per such source, holding the token level, when
it was last refilled, and how long a ``429`` from the source blocks requests.
Every process fetching from the source takes its tokens from this row.

The downgrade drops the table.

Revision ID: d3e4f5a6b7c9
Revises: c2d3e4f5a6b8
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c9"  # pragma: allowlist secret
down_revision: str | None = "c2d3e4f5a6b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "catalog_source_buckets",
        sa.Column(
            "id",
            sa.String(length=30),
            server_default=sa.text("generate_ksuid('cbkt')") if pg else None,
            nullable=False,
        ),
        sa.Column("source_slug", sa.String(length=32), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("refilled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_slug", name="uq_catalog_source_buckets_source_slug"),
    )


def downgrade() -> None:
    op.drop_table("catalog_source_buckets")
