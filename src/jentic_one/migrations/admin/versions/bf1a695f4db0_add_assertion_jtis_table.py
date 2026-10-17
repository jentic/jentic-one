"""add assertion_jtis table

Adds ``assertion_jtis`` to the admin schema: one row per accepted RFC 7523
JWT-bearer assertion, keyed on ``(issuer, jti_digest)`` by the unique index
``ix_assertion_jtis_issuer_jti_digest``, so a replayed ``jti`` is refused by
every auth process that shares the admin database. ``ix_assertion_jtis_expires_at``
serves the bounded prune of expired rows on the exchange path.

The table starts empty. Downgrade drops it; the rows are short-lived replay
records (minutes), so nothing of lasting value is lost.

Revision ID: bf1a695f4db0
Revises: c00de2f5d777
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bf1a695f4db0"  # pragma: allowlist secret
down_revision: str | None = "c00de2f5d777"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "assertion_jtis"


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("ajti") if pg else None,
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("jti_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assertion_jtis_issuer_jti_digest", _TABLE, ["issuer", "jti_digest"], unique=True
    )
    op.create_index("ix_assertion_jtis_expires_at", _TABLE, ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_assertion_jtis_expires_at", table_name=_TABLE)
    op.drop_index("ix_assertion_jtis_issuer_jti_digest", table_name=_TABLE)
    op.drop_table(_TABLE)
