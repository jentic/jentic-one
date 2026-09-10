"""make execution_records.toolkit_id nullable-legacy

Theme 5, Phase 2 (broker cutover): direct-binding executions have no
mediating toolkit — their consumer attribution is ``credential_id`` — so the
column becomes nullable rather than storing a lying empty string. Legacy
toolkit-path executions keep writing their toolkit id unchanged; the column
is retired with toolkits (Phase 4+).

Downgrade backfills NULLs to '' before restoring NOT NULL so it cannot fail
on direct-path rows (matching the pre-Phase-2 "empty string when unset"
convention).

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("execution_records") as batch_op:
        batch_op.alter_column(
            "toolkit_id",
            existing_type=sa.String(30),
            nullable=True,
        )


def downgrade() -> None:
    op.execute("UPDATE execution_records SET toolkit_id = '' WHERE toolkit_id IS NULL")
    with op.batch_alter_table("execution_records") as batch_op:
        batch_op.alter_column(
            "toolkit_id",
            existing_type=sa.String(30),
            nullable=False,
        )
