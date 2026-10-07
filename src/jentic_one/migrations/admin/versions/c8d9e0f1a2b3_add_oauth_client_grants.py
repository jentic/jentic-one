"""add oauth_client_grants and grant lineage columns

Phase-3a-3 (design §4.1/§4.4-4.5, D2/D3/D4): the consent→agent binding.

- New ``oauth_client_grants`` table: one row per consent of a
  ``consent_model='agent'`` client — ksuid ``ocg`` id, the client's PUBLIC
  ``client_id`` string (the token-lineage join key, D3), the consenting
  ``user_id``, the bound ``agent_id`` (exactly one, D2), the D2-intersected
  ``scopes`` set, ``status`` (``active``|``revoked``), ``revoked_at``,
  ``last_used_at``, AuditableMixin timestamps. Non-unique indexes on
  ``oauth_client_id`` / ``agent_id`` / ``user_id``.
- ``authorization_codes.grant_id`` (nullable): consent-approve stamps the
  freshly-minted grant on the code so the token exchange can bind to it.
- ``access_tokens.oauth_grant_id`` + ``refresh_tokens.oauth_grant_id``
  (nullable, indexed): grant-channel lineage next to ``oauth_client_id``
  (D4) — resolvers gate on it live and grant ``:revoke`` sweeps by it.

Additive only. NULL in every new column means "pre-3a-3 / act-as-user row",
so the existing act-as-user paths stay byte-identical.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "oauth_client_grants",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function so inserts are expected to go through the ORM model,
            # whose Python-side default (generate_ksuid("ocg")) supplies it.
            server_default=sa.func.generate_ksuid("ocg") if pg else None,
            nullable=False,
        ),
        sa.Column("oauth_client_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(30), nullable=False),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="active"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "ix_oauth_client_grants_oauth_client_id", "oauth_client_grants", ["oauth_client_id"]
    )
    op.create_index("ix_oauth_client_grants_agent_id", "oauth_client_grants", ["agent_id"])
    op.create_index("ix_oauth_client_grants_user_id", "oauth_client_grants", ["user_id"])
    op.create_index("ix_oauth_client_grants_created_at", "oauth_client_grants", ["created_at"])
    op.create_index("ix_oauth_client_grants_created_by", "oauth_client_grants", ["created_by"])

    op.add_column("authorization_codes", sa.Column("grant_id", sa.String(30), nullable=True))
    op.add_column("access_tokens", sa.Column("oauth_grant_id", sa.String(30), nullable=True))
    op.create_index("ix_access_tokens_oauth_grant_id", "access_tokens", ["oauth_grant_id"])
    op.add_column("refresh_tokens", sa.Column("oauth_grant_id", sa.String(30), nullable=True))
    op.create_index("ix_refresh_tokens_oauth_grant_id", "refresh_tokens", ["oauth_grant_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_oauth_grant_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "oauth_grant_id")
    op.drop_index("ix_access_tokens_oauth_grant_id", table_name="access_tokens")
    op.drop_column("access_tokens", "oauth_grant_id")
    op.drop_column("authorization_codes", "grant_id")
    op.drop_index("ix_oauth_client_grants_created_by", table_name="oauth_client_grants")
    op.drop_index("ix_oauth_client_grants_created_at", table_name="oauth_client_grants")
    op.drop_index("ix_oauth_client_grants_user_id", table_name="oauth_client_grants")
    op.drop_index("ix_oauth_client_grants_agent_id", table_name="oauth_client_grants")
    op.drop_index("ix_oauth_client_grants_oauth_client_id", table_name="oauth_client_grants")
    op.drop_table("oauth_client_grants")
