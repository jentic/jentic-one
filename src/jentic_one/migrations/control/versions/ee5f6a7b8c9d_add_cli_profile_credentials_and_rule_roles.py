"""add credentials.cli_profile and agent_permission_rules.api_role

``credentials.cli_profile`` names the CLI profile a profile-scoped credential
belongs to (``github``): one connect, one secret, covering exactly the APIs the
shipped manifest declares, resolved at use time. NULL for every other
credential, whose coverage is unchanged.

``agent_permission_rules.api_role`` names the profile API (``rest``,
``graphql``, ``git``, ``uploads``) a rule applies to on such a credential's
binding. NULL means "the role the rule's kind implies"; on any other
credential the column is ignored. Shared rule sets store no role (their rules
go by kind).

Both are nullable with no default, so no backfill and no table rewrite.

Revision ID: ee5f6a7b8c9d
Revises: dd4e5f6a7b8c
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ee5f6a7b8c9d"  # pragma: allowlist secret
down_revision: str | None = "dd4e5f6a7b8c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credentials", sa.Column("cli_profile", sa.String(64), nullable=True))
    op.add_column("agent_permission_rules", sa.Column("api_role", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_permission_rules") as batch:
        batch.drop_column("api_role")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("cli_profile")
