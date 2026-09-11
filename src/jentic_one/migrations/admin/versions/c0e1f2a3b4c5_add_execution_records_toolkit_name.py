"""execution_records: add denormalized toolkit_name (theme-5 phase 6b)

The human-readable name for historical toolkit-path executions. Resolution
used to happen at read time against ``control.toolkits``; that table is
dropped in this release, so the name is denormalized onto the execution row
instead. The Phase-6a flattening job backfills it (app-level cross-DB copy:
control toolkit names → this column wherever ``toolkit_id`` is set and the
name is NULL) **before** the drop; rows whose toolkit was already deleted
keep NULL, exactly as the read-time resolver reported them.

Idempotent: the flattening job adds this column itself when it runs before
this migration (the documented runbook order is flatten-then-migrate, and a
single ``migrations.run`` invocation migrates control — where the gated drop
raises pre-acknowledgement — before admin, so the job cannot rely on this
migration having run). Both paths create the identical shape; whichever runs
first wins and the other no-ops.

Revision ID: c0e1f2a3b4c5
Revises: b9d0e1f2a3b4
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0e1f2a3b4c5"  # pragma: allowlist secret
down_revision: str | None = "b9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(bind: sa.engine.Connection) -> bool:
    columns = sa.inspect(bind).get_columns("execution_records")
    return any(column["name"] == "toolkit_name" for column in columns)


def upgrade() -> None:
    if _column_exists(op.get_bind()):
        return
    op.add_column(
        "execution_records",
        sa.Column("toolkit_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    if not _column_exists(op.get_bind()):
        return
    op.drop_column("execution_records", "toolkit_name")
