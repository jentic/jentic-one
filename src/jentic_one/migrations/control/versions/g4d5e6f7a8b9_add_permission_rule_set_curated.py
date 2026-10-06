"""add curated to permission_rule_sets

``curated`` marks a shared rule set an ``org:admin`` (or a system job)
created. A curated set is attachable to a binding by any
``credentials:write`` holder and editable only by ``org:admin``; any other set
is attachable and editable by its creator or ``org:admin``. The flag is
recorded at creation, so a later change to the creator's permissions does not
change who may attach the set.

Schema only. Existing rows start as not curated; the
``rule_sets_mark_curated`` upgrade step marks the sets whose creator is a
system actor or holds ``org:admin``. It reads the admin database, so it runs
from the migration runner rather than from this Alembic tree.

Revision ID: g4d5e6f7a8b9
Revises: f3c4d5e6a7b8
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g4d5e6f7a8b9"  # pragma: allowlist secret
down_revision: str | None = "f3c4d5e6a7b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "permission_rule_sets",
        sa.Column("curated", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("permission_rule_sets", "curated")
