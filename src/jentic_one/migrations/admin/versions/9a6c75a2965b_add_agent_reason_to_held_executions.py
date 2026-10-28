"""add agent_reason to held_executions

The agent's own stated reason for a held call (its ``Jentic-Reason`` request
header), shown to the reviewer. The column is nullable, so adding it rewrites
no rows and existing holds read as "no reason given".

Revision ID: 9a6c75a2965b
Revises: e5c9f3a7b802
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a6c75a2965b"  # pragma: allowlist secret
down_revision: str | None = "e5c9f3a7b802"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "held_executions"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("agent_reason", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "agent_reason")
