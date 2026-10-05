"""widen permission rule effect to 16 chars

Widens the ``effect`` column on ``agent_permission_rules`` and
``permission_rule_set_rules`` from ``VARCHAR(10)`` to ``VARCHAR(16)`` so the
new ``require-approval`` value (16 characters) fits. The previous maximum
was ``allow`` / ``deny`` (5 and 4 characters respectively).

Revision ID: 9f7b048514c6
Revises: f3c4d5e6a7b8
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f7b048514c6"  # pragma: allowlist secret
down_revision: str | None = "f3c4d5e6a7b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "agent_permission_rules",
        "effect",
        existing_type=sa.String(10),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.alter_column(
        "permission_rule_set_rules",
        "effect",
        existing_type=sa.String(10),
        type_=sa.String(16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "permission_rule_set_rules",
        "effect",
        existing_type=sa.String(16),
        type_=sa.String(10),
        existing_nullable=False,
    )
    op.alter_column(
        "agent_permission_rules",
        "effect",
        existing_type=sa.String(16),
        type_=sa.String(10),
        existing_nullable=False,
    )
