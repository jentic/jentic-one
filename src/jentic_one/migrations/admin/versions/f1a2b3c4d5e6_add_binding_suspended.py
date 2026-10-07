"""add suspended to agent_credential_bindings

Reversible per-consumer cut-off (theme 5, phase 1 — PR #35 review): a
suspended binding is excluded from broker derivation but keeps its
permission rules, so restoring access is a flag flip rather than a
destructive re-bind that loses authored policy. Unbind soft-suspends by
default; row deletion is an explicit purge.

Revision ID: f1a2b3c4d5e6
Revises: d9e0f1a2b3c4
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_credential_bindings",
        sa.Column(
            "suspended",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_credential_bindings", "suspended")
