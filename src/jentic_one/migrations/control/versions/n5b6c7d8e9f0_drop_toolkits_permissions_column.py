"""drop toolkits.permissions (never enforced)

Revision ID: n5b6c7d8e9f0
Revises: m4a5b6c7d8e9
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n5b6c7d8e9f0"
down_revision: str | None = "m4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``toolkits.permissions`` (a JSON column populated on ``POST /toolkits``)
    # was write-only from day one — the broker's rule evaluator reads the
    # per-binding ``toolkit_permission_rules`` table exclusively and never
    # consulted this column. Silently accepting the field on create misled
    # operators into thinking they had gated a toolkit when they hadn't
    # (issue #655). ``POST /toolkits`` now rejects the field with 422 and the
    # column is retired in the same release. No data migration: the payloads
    # never had enforcement semantics, so there is nothing meaningful to
    # migrate to the enforced surface — operators re-authoring rules attach
    # them to the intended binding via
    # ``PUT /toolkits/{id}/credentials/{id}/permissions``.
    op.drop_column("toolkits", "permissions")


def downgrade() -> None:
    # Restores the column with its original nullable=False + empty-list
    # default so a rollback lands on a schema-shape identical to the
    # pre-#655 state. Existing rows are back-filled with the empty list;
    # the historical payloads are not restored (see upgrade rationale).
    op.add_column(
        "toolkits",
        sa.Column(
            "permissions",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
