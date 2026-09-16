"""add operation path and method to execution_records

Nullable-legacy adds: ``operation_path`` (the spec's path template, truncated to
512 at the write seam) and ``operation_method`` (width mirrors registry
``operations.method``). Nullable because historical rows and legacy in-flight
job payloads carry only ``operation_id`` — no backfill, no lock concern.
Deliberately unindexed: nothing filters or groups by these columns (list
filters use toolkit/trace/status/api/actor; monitoring groups by
``operation_id``); they are display-only.

Revision ID: c1d2e3f4a5b6
Revises: b9d0e1f2a3b4
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"  # pragma: allowlist secret
down_revision: str | None = "b9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_records", sa.Column("operation_path", sa.String(512), nullable=True))
    op.add_column("execution_records", sa.Column("operation_method", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_records", "operation_method")
    op.drop_column("execution_records", "operation_path")
