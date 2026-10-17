"""widen catalog_api_id on credentials and oauth_app_registrations to 320

Both columns hold a copy of the registry's ``catalog_api_id``, which a catalog
source derives from fields up to 320 characters long. Widening never loses
data; the downgrade narrows back to 255 and fails on PostgreSQL if a longer
value is stored.

Revision ID: ee5f6a7b8c9d
Revises: dd4e5f6a7b8c
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ee5f6a7b8c9d"  # pragma: allowlist secret
down_revision: str | None = "dd4e5f6a7b8c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (("credentials", True), ("oauth_app_registrations", False))


def _alter(new: int, old: int) -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    for table, nullable in _COLUMNS:
        kwargs = {
            "type_": sa.String(new),
            "existing_type": sa.String(old),
            "existing_nullable": nullable,
        }
        if pg:
            op.alter_column(table, "catalog_api_id", **kwargs)  # type: ignore[arg-type]
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column("catalog_api_id", **kwargs)  # type: ignore[arg-type]


def upgrade() -> None:
    _alter(320, 255)


def downgrade() -> None:
    _alter(255, 320)
