"""widen permission rule effect to 16 chars

Widens the ``effect`` column on ``agent_permission_rules`` and
``permission_rule_set_rules`` from ``VARCHAR(10)`` to ``VARCHAR(16)`` so the
new ``require-approval`` value (16 characters) fits. The previous maximum
was ``allow`` / ``deny`` (5 and 4 characters respectively).

Revision ID: 9f7b048514c6
Revises: aa1b2c3d4e5f
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f7b048514c6"  # pragma: allowlist secret
down_revision: str | None = "aa1b2c3d4e5f"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent_permission_rules", "permission_rule_set_rules")


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    for table in _TABLES:
        if pg:
            op.alter_column(
                table,
                "effect",
                existing_type=sa.String(10),
                type_=sa.String(16),
                existing_nullable=False,
            )
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "effect",
                    existing_type=sa.String(10),
                    type_=sa.String(16),
                    existing_nullable=False,
                )


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    for table in reversed(_TABLES):
        if pg:
            op.alter_column(
                table,
                "effect",
                existing_type=sa.String(16),
                type_=sa.String(10),
                existing_nullable=False,
            )
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "effect",
                    existing_type=sa.String(16),
                    type_=sa.String(10),
                    existing_nullable=False,
                )
