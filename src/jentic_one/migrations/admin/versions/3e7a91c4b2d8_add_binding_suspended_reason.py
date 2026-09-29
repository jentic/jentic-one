"""add suspended_reason to agent_credential_bindings

Records why a binding is suspended. ``NULL`` means a manual suspension
(the default unbind); ``api_deleted`` means the registry suspended the
binding because the API its credential serves was deleted, so a later
re-import under the same identity does not re-adopt it. Resuming a binding
clears the reason.

Additive and nullable: existing rows keep ``NULL`` (manual) and need no
backfill. Batch-safe on SQLite (``render_as_batch``).

Revision ID: 3e7a91c4b2d8
Revises: c0d1e2f3a4b5
Create Date: 2026-09-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3e7a91c4b2d8"  # pragma: allowlist secret
down_revision: str | None = "c0d1e2f3a4b5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_credential_bindings",
        sa.Column("suspended_reason", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_credential_bindings", "suspended_reason")
