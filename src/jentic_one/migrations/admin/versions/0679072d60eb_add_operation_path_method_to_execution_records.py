"""add operation path and method to execution_records

Nullable-legacy adds: ``operation_path`` (the spec's path template, Text to
mirror its unbounded registry source ``operations.path``) and
``operation_method`` (width mirrors registry ``operations.method``). Nullable
because historical rows and legacy in-flight job payloads carry only
``operation_id`` — no backfill, no lock concern.
Deliberately unindexed: nothing filters or groups by these columns (list
filters use toolkit/trace/status/api/actor; monitoring groups by
``operation_id``); they are display-only.

Revision ID: 0679072d60eb
Revises: 5c7e2a9d4f16
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0679072d60eb"  # pragma: allowlist secret
down_revision: str | None = "5c7e2a9d4f16"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_records", sa.Column("operation_path", sa.Text(), nullable=True))
    op.add_column("execution_records", sa.Column("operation_method", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_records", "operation_method")
    op.drop_column("execution_records", "operation_path")
