"""add catalog_api_id to credentials

Revision ID: o6c7d8e9f0a1
Revises: n5b6c7d8e9f0
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o6c7d8e9f0a1"
down_revision: str | None = "n5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Catalog identity slug of the credential's target API, copied verbatim at
    # create time when the client knows it (#910). Display-only — resolution
    # identity stays the (api_vendor, api_name, api_version) tuple. Nullable:
    # pre-existing credentials and manually-imported APIs have none.
    op.add_column("credentials", sa.Column("catalog_api_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("credentials", "catalog_api_id")
