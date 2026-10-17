"""widen apis.catalog_api_id to 320 characters

A catalog source derives ``catalog_api_id`` from fields up to 320 characters
long (``catalog_entries.catalog_api_id`` is ``String(320)``), so the copy kept on
an imported API must fit the same width. Widening never loses data; the
downgrade narrows back to 255 and fails on PostgreSQL if a longer value is
stored.

Revision ID: b1c2d3e4f5a7
Revises: a0b1c2d3e4f6
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a7"  # pragma: allowlist secret
down_revision: str | None = "a0b1c2d3e4f6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _alter(new: int, old: int) -> None:
    kwargs = {"type_": sa.String(new), "existing_type": sa.String(old), "existing_nullable": True}
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("apis", "catalog_api_id", **kwargs)  # type: ignore[arg-type]
    else:
        with op.batch_alter_table("apis") as batch:
            batch.alter_column("catalog_api_id", **kwargs)  # type: ignore[arg-type]


def upgrade() -> None:
    _alter(320, 255)


def downgrade() -> None:
    _alter(255, 320)
