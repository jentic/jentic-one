"""add latest_releases

Single-row table holding the last-known-latest ``jentic-one`` release the CLI
(``jenticctl update``) has reported to this backend. The public
``GET /system/version`` reads it (alongside the running version) so the UI can
show an "update available" banner. Keyed by a ksuid ``id`` with a unique natural
``key`` (always ``"latest_release"``); the singleton is enforced at the repo
layer by upserting on ``key``.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"  # pragma: allowlist secret
down_revision: str | None = "d0e1f2a3b4c5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "latest_releases",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such function
            # so inserts must go through the ORM model (Python default supplies id).
            server_default=sa.func.generate_ksuid("lrel") if pg else None,
            nullable=False,
        ),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_latest_releases_key", "latest_releases", ["key"], unique=True)
    op.create_index("ix_latest_releases_created_at", "latest_releases", ["created_at"])
    op.create_index("ix_latest_releases_created_by", "latest_releases", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_latest_releases_created_by", table_name="latest_releases")
    op.drop_index("ix_latest_releases_created_at", table_name="latest_releases")
    op.drop_index("ix_latest_releases_key", table_name="latest_releases")
    op.drop_table("latest_releases")
