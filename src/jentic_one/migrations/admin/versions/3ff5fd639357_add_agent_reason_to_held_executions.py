"""add agent_reason to held_executions

Adds a nullable ``agent_reason`` column to ``held_executions``: the filing
agent's own account of why it made the held call, set once by
``POST /executions/holds/{id}:explain`` and shown to the reviewer.

Existing rows remain ``NULL`` (not explained). No backfill or index is
required. Downgrade drops the column, and with it every reason: they are
advisory text shown to reviewers, not part of any decision record.

Revision ID: 3ff5fd639357
Revises: c7d2e9a41b30
Create Date: 2026-11-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3ff5fd639357"  # pragma: allowlist secret
down_revision: str | None = "c7d2e9a41b30"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("held_executions", sa.Column("agent_reason", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("held_executions", "agent_reason")
