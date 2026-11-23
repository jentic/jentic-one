"""track completion of individual credential connect flows

Revision ID: e9f0a1b2c3d4
Revises: d9305dcd765e
Create Date: 2026-04-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"  # pragma: allowlist secret
down_revision: str | None = "d9305dcd765e"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("connect_state_nonces", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("device_authorization_credentials", sa.Column("flow_nonce", sa.String(64)))


def downgrade() -> None:
    op.drop_column("device_authorization_credentials", "flow_nonce")
    op.drop_column("connect_state_nonces", "completed_at")
