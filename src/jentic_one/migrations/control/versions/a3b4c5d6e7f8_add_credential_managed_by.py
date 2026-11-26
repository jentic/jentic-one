"""add managed_by marker to credentials

Two nullable columns record that an in-process extension manages a
credential: ``managed_by_kind`` (the kind of managing object) and
``managed_by_ref`` (its id). Both are set together or not at all
(``ck_credentials_managed_by_pair``). Existing rows stay NULL, i.e. unmanaged.

Revision ID: a3b4c5d6e7f8
Revises: e9f0a1b2c3d4
Create Date: 2026-11-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"  # pragma: allowlist secret
down_revision: str | None = "e9f0a1b2c3d4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_credentials_managed_by_pair"
_CHECK_SQL = "(managed_by_kind IS NULL) = (managed_by_ref IS NULL)"
_INDEX_NAME = "ix_credentials_managed_by"


def upgrade() -> None:
    kind = sa.Column("managed_by_kind", sa.String(50), nullable=True)
    ref = sa.Column("managed_by_ref", sa.String(255), nullable=True)
    if op.get_bind().dialect.name == "postgresql":
        op.add_column("credentials", kind)
        op.add_column("credentials", ref)
        op.create_check_constraint(_CHECK_NAME, "credentials", _CHECK_SQL)
    else:
        # SQLite adds a CHECK constraint only by rebuilding the table.
        with op.batch_alter_table("credentials") as batch:
            batch.add_column(kind)
            batch.add_column(ref)
            batch.create_check_constraint(_CHECK_NAME, _CHECK_SQL)
    op.create_index(_INDEX_NAME, "credentials", ["managed_by_kind", "managed_by_ref"])


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="credentials")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_CHECK_NAME, "credentials", type_="check")
        op.drop_column("credentials", "managed_by_ref")
        op.drop_column("credentials", "managed_by_kind")
    else:
        with op.batch_alter_table("credentials") as batch:
            batch.drop_constraint(_CHECK_NAME, type_="check")
            batch.drop_column("managed_by_ref")
            batch.drop_column("managed_by_kind")
