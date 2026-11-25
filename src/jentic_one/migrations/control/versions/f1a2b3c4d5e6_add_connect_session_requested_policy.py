"""add requested_policy to connect_sessions

A connect request can ask for how long the access it requests should last.
The ask is stored on the session (``requested_policy``, JSON, nullable) like
the requested rules and scopes: it is only the initiator's request, and the
approver decides the binding's actual policy when confirming. ``NULL`` (every
existing row) means nothing was requested.

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-11-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"  # pragma: allowlist secret
down_revision: str | None = "e9f0a1b2c3d4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connect_sessions",
        sa.Column(
            "requested_policy",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("connect_sessions", "requested_policy")
