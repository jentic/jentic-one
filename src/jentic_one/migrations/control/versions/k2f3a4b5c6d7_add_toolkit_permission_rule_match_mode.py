"""add match_mode to toolkit_permission_rules

Revision ID: k2f3a4b5c6d7
Revises: j1e2f3a4b5c6
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k2f3a4b5c6d7"
down_revision: str | None = "j1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add ``match_mode`` to ``toolkit_permission_rules`` so a rule can declare
    # how its ``path`` is interpreted (``regex``/``prefix``/``exact``).
    # NOT NULL with ``server_default 'regex'`` back-fills existing rows in a
    # single statement — issue #751. NOTE: the column name is ``match_mode``,
    # not ``match``, because ``MATCH`` is a reserved word in SQLite.
    op.add_column(
        "toolkit_permission_rules",
        sa.Column(
            "match_mode",
            sa.String(length=10),
            nullable=False,
            server_default="regex",
        ),
    )


def downgrade() -> None:
    op.drop_column("toolkit_permission_rules", "match_mode")
