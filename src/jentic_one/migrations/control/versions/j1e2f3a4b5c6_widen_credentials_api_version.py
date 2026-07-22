"""widen credentials.api_version to 100 chars

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-07-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j1e2f3a4b5c6"
down_revision: str | None = "i0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Registry versions are uncapped (apis.version is VARCHAR(100)); SNAPSHOT
    # builds carry a commit-hash suffix that overflows the old VARCHAR(50) and
    # 500s on credential create (#690). Align with apis.version.
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        op.alter_column(
            "credentials",
            "api_version",
            type_=sa.String(100),
            existing_type=sa.String(50),
            existing_nullable=True,
        )
    else:
        with op.batch_alter_table("credentials") as batch:
            batch.alter_column(
                "api_version",
                type_=sa.String(100),
                existing_type=sa.String(50),
                existing_nullable=True,
            )


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        op.alter_column(
            "credentials",
            "api_version",
            type_=sa.String(50),
            existing_type=sa.String(100),
            existing_nullable=True,
        )
    else:
        with op.batch_alter_table("credentials") as batch:
            batch.alter_column(
                "api_version",
                type_=sa.String(50),
                existing_type=sa.String(100),
                existing_nullable=True,
            )
