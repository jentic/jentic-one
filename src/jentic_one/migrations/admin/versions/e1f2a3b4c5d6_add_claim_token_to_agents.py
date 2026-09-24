"""add claim_token_hash + claim_expires_at to agents

Self-registered (DCR) agent ownership claiming. ``/register`` mints an opaque
claim token when a claim-token minter is installed (auth/core/claim.py); only its
sha256 hash + expiry are stored here, and the registering human presents the
plaintext to ``POST /agents/{id}:claim`` to set ``owner_id``. Kept separate from
the RAT columns (which set_approval nulls) so approval never drops the token.

Both columns are nullable with no backfill — existing agents simply have no claim
token, which matches the OSS default (no minter installed → no claim flow).

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"  # pragma: allowlist secret
down_revision: str | None = "d0e1f2a3b4c5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("claim_token_hash", sa.String(64), nullable=True))
    op.add_column(
        "agents", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agents", "claim_expires_at")
    op.drop_column("agents", "claim_token_hash")
