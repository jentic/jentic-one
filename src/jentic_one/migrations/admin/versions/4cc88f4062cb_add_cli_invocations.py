"""add cli_invocations table

Adds the ``cli_invocations`` table to the admin schema: one row per vendor CLI
invocation token (``cit_``), stored as a SHA-256 hash with the hash of the
parent ``at_`` access token that minted it. A new, empty table, so the upgrade
takes no lock on existing data; the downgrade drops it and every outstanding
invocation token with it.

Revision ID: 4cc88f4062cb
Revises: e5c9f3a7b802
Create Date: 2026-10-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4cc88f4062cb"  # pragma: allowlist secret
down_revision: str | None = "e5c9f3a7b802"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "cli_invocations",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("cli") if pg else None,
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("parent_token_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(30), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("vendor", sa.String(32), nullable=False),
        sa.Column("credential_name", sa.String(255), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("command_title", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hard_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cli_invocations_token_hash", "cli_invocations", ["token_hash"], unique=True)
    op.create_index("ix_cli_invocations_actor_id", "cli_invocations", ["actor_id"])
    op.create_index("ix_cli_invocations_hard_expires_at", "cli_invocations", ["hard_expires_at"])
    op.create_index("ix_cli_invocations_created_at", "cli_invocations", ["created_at"])
    op.create_index("ix_cli_invocations_created_by", "cli_invocations", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_cli_invocations_created_by", table_name="cli_invocations")
    op.drop_index("ix_cli_invocations_created_at", table_name="cli_invocations")
    op.drop_index("ix_cli_invocations_hard_expires_at", table_name="cli_invocations")
    op.drop_index("ix_cli_invocations_actor_id", table_name="cli_invocations")
    op.drop_index("ix_cli_invocations_token_hash", table_name="cli_invocations")
    op.drop_table("cli_invocations")
