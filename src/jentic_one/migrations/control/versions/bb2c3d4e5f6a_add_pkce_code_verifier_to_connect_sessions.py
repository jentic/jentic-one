"""add pkce_code_verifier to connect_sessions

Adds a nullable ``pkce_code_verifier`` column to ``connect_sessions``. The
auth-code flow now generates a PKCE (RFC 7636) code verifier at ``begin``
and includes the derived S256 challenge on the authorize URL; the vendor
echoes the verifier back at token-exchange time. The verifier is stored
server-side (never in the state JWT, which transits the browser) so it
survives the redirect gap between ``begin`` and ``complete_from_callback``.

Nullable: device flow never sets it, and auth-code sessions created before
this column existed complete without PKCE (only newly-initiated flows
opt in).

Revision ID: bb2c3d4e5f6a
Revises: aa1b2c3d4e5f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bb2c3d4e5f6a"  # pragma: allowlist secret
down_revision: str | None = "aa1b2c3d4e5f"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connect_sessions",
        sa.Column("pkce_code_verifier", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connect_sessions", "pkce_code_verifier")
