"""add sigv4_credentials table

Revision ID: o6c7d8e9f0a1
Revises: n5b6c7d8e9f0
Create Date: 2026-07-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o6c7d8e9f0a1"
down_revision: str | None = "n5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "sigv4_credentials",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("sigv4") if pg else None,
            nullable=False,
        ),
        sa.Column(
            "credential_id",
            sa.String(30),
            sa.ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("access_key_id", sa.String(128), nullable=False),
        sa.Column("encrypted_secret_access_key", sa.Text, nullable=False),
        sa.Column("secret_preview", sa.String(16), nullable=True),
        sa.Column("encrypted_session_token", sa.Text, nullable=True),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
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
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sigv4_credentials_credential_id", "sigv4_credentials", ["credential_id"])
    op.create_index("ix_sigv4_credentials_created_at", "sigv4_credentials", ["created_at"])
    op.create_index("ix_sigv4_credentials_created_by", "sigv4_credentials", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_sigv4_credentials_created_by", table_name="sigv4_credentials")
    op.drop_index("ix_sigv4_credentials_created_at", table_name="sigv4_credentials")
    op.drop_index("ix_sigv4_credentials_credential_id", table_name="sigv4_credentials")
    op.drop_table("sigv4_credentials")
