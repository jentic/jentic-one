"""add credential attribution columns to execution_records (#740)

Add nullable ``credential_id`` / ``credential_name`` columns so an execution
record attributes back to the stored credential the broker actually used.
Nullable is semantically honest: historical rows, executions using inline
auth, and executions that failed before credential resolution all leave
both ``NULL``. No FK: ``credentials`` lives in the control DB (cross-DB,
same as ``toolkit_id`` here). Indexed on ``credential_id`` for the audit
console's "what did this credential do?" query.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"  # pragma: allowlist secret
down_revision: str | None = "c9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_records", sa.Column("credential_id", sa.String(30), nullable=True))
    op.add_column("execution_records", sa.Column("credential_name", sa.String(255), nullable=True))
    op.create_index("ix_execution_records_credential_id", "execution_records", ["credential_id"])


def downgrade() -> None:
    op.drop_index("ix_execution_records_credential_id", table_name="execution_records")
    op.drop_column("execution_records", "credential_name")
    op.drop_column("execution_records", "credential_id")
