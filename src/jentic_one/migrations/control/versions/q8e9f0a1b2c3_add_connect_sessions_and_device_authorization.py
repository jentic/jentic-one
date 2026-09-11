"""add connect_sessions + device_authorization_credentials + credentials.state

Backs the agent-driven integration flow: a new
`connect_sessions` table drives the state machine, `device_authorization_credentials`
holds RFC 8628 registration + polling state per credential, a new
`agent_credential_permissions` junction records permission rules for the
theme-5 target model, and `credentials.state` distinguishes pending from
connected rows so the broker can skip half-formed credentials.

Revision ID: q8e9f0a1b2c3
Revises: p7d8e9f0a1b2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q8e9f0a1b2c3"
down_revision: str | None = "p7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    json_type = sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

    # 1. Add credentials.state (backfill existing rows as "connected").
    op.add_column(
        "credentials",
        sa.Column(
            "state",
            sa.String(30),
            server_default=sa.text("'connected'"),
            nullable=False,
        ),
    )

    # 2. connect_sessions — flow-agnostic session record.
    op.create_table(
        "connect_sessions",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("cs") if pg else None,
            nullable=False,
        ),
        sa.Column(
            "credential_id",
            sa.String(30),
            sa.ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vendor", sa.String(255), nullable=False),
        # Nullable until credentials bind directly to agents (see the
        # ``connect_sessions`` ORM model). Will become NOT NULL once
        # agent-credential bindings replace toolkit membership.
        sa.Column("agent_id", sa.String(30), nullable=True),
        sa.Column("initiator_actor_id", sa.String(30), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("preferred_flow", sa.String(50), nullable=True),
        sa.Column("resolved_flow", sa.String(50), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=True),
        sa.Column("connected_as", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("poll_token", sa.String(64), nullable=False),
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
        "ix_connect_sessions_agent",
        "connect_sessions",
        ["agent_id", "state"],
    )
    op.create_index(
        "ix_connect_sessions_credential",
        "connect_sessions",
        ["credential_id"],
    )
    op.create_index(
        "ix_connect_sessions_poll_token",
        "connect_sessions",
        ["poll_token"],
        unique=True,
    )

    # 3. device_authorization_credentials — per-credential device-flow registration
    # + transient polling state.
    op.create_table(
        "device_authorization_credentials",
        sa.Column(
            "id",
            sa.String(30),
            sa.ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("token_url", sa.String(2048), nullable=False),
        sa.Column("authorization_endpoint", sa.String(2048), nullable=False),
        sa.Column("encrypted_device_code", sa.Text, nullable=True),
        sa.Column("user_code", sa.String(50), nullable=True),
        sa.Column("verification_uri", sa.String(2048), nullable=True),
        sa.Column("verification_uri_complete", sa.String(2048), nullable=True),
        sa.Column("poll_interval_seconds", sa.Integer, nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_scopes", json_type, nullable=True),
        sa.Column("granted_scopes", json_type, nullable=True),
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

    # 4. agent_credential_permissions — junction table for theme-5's target model.
    op.create_table(
        "agent_credential_permissions",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("acp") if pg else None,
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column(
            "credential_id",
            sa.String(30),
            sa.ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rules",
            json_type,
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
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
        sa.UniqueConstraint("agent_id", "credential_id", name="uq_agent_credential"),
    )
    op.create_index("ix_acp_agent", "agent_credential_permissions", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_acp_agent", table_name="agent_credential_permissions")
    op.drop_table("agent_credential_permissions")
    op.drop_table("device_authorization_credentials")
    op.drop_index("ix_connect_sessions_poll_token", table_name="connect_sessions")
    op.drop_index("ix_connect_sessions_credential", table_name="connect_sessions")
    op.drop_index("ix_connect_sessions_agent", table_name="connect_sessions")
    op.drop_table("connect_sessions")
    op.drop_column("credentials", "state")
