"""rewrite require-approval rule effects to deny on downgrade

The data step paired with ``9f7b048514c6`` (which widens ``effect`` to
``VARCHAR(16)`` for ``require-approval``). Upgrading changes no data. On
downgrade, every ``require-approval`` rule on ``agent_permission_rules`` and
``permission_rule_set_rules`` becomes ``deny`` before ``9f7b048514c6``
narrows the column back to ``VARCHAR(10)``: the value would not fit, and a
schema without the approval tier must still refuse a call that needed
approval rather than allow it.

Revision ID: 70ff607085b4
Revises: 9f7b048514c6
Create Date: 2026-10-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "70ff607085b4"  # pragma: allowlist secret
down_revision: str | None = "9f7b048514c6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent_permission_rules", "permission_rule_set_rules")


def upgrade() -> None:
    """No data changes on upgrade: existing rules keep their effect."""


def downgrade() -> None:
    for table in _TABLES:
        op.execute(sa.text(f"UPDATE {table} SET effect = 'deny' WHERE effect = 'require-approval'"))
