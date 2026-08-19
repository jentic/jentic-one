"""add oauth_clients

OAuth client registry for third-party applications. Stores client_id and
allowed redirect_uris per client. No client_secret — PKCE (S256) provides
sufficient security for public clients.

Revision ID: aa6b7c8d9e0f
Revises: e1f2a3b4c5d6
Create Date: 2026-08-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "aa6b7c8d9e0f"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "oauth_clients",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("oac") if pg else None,
            nullable=False,
        ),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "redirect_uris",
            postgresql.ARRAY(sa.String(2048)) if pg else sa.JSON(),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("require_consent", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oauth_clients_client_id", "oauth_clients", ["client_id"], unique=True)
    op.create_index("ix_oauth_clients_created_at", "oauth_clients", ["created_at"])
    op.create_index("ix_oauth_clients_created_by", "oauth_clients", ["created_by"])
    op.create_index("ix_oauth_clients_active", "oauth_clients", ["active"])


def downgrade() -> None:
    op.drop_index("ix_oauth_clients_active", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_created_by", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_created_at", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_client_id", table_name="oauth_clients")
    op.drop_table("oauth_clients")
