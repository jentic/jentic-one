"""add oauth_app_registrations + owner_user_id/oauth_app_registration_id on credentials

Adds a class-table-inheritance triple for admin-configured OAuth app
registrations that any user on the instance can SSO through, plus two
columns on ``credentials`` that record which registration minted a grant
(``oauth_app_registration_id``) and who owns the resulting connection
(``owner_user_id``). A user may connect multiple times through the same
registration (one credential per agent / environment / etc.), so no unique
constraint is placed on the pair.

Revision ID: aa1b2c3d4e5f
Revises: z7b8c9d0e1f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aa1b2c3d4e5f"  # pragma: allowlist secret
down_revision: str | None = "z7b8c9d0e1f2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    json_type = sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

    # 1. Base table — one row per registered OAuth app at a vendor.
    op.create_table(
        "oauth_app_registrations",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("oar") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_vendor", sa.String(100), nullable=False),
        sa.Column("flow_kind", sa.String(50), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
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
    )
    op.create_index(
        "ix_oauth_app_registrations_api_vendor",
        "oauth_app_registrations",
        ["api_vendor"],
    )
    op.create_index(
        "ix_oauth_app_registrations_flow_kind",
        "oauth_app_registrations",
        ["flow_kind"],
    )
    op.create_index(
        "ix_oauth_app_registrations_created_at",
        "oauth_app_registrations",
        ["created_at"],
    )
    op.create_index(
        "ix_oauth_app_registrations_created_by",
        "oauth_app_registrations",
        ["created_by"],
    )

    # 2. Auth-code extension — 1:1 with the base for auth-code rows.
    op.create_table(
        "authorization_code_app_registration_details",
        sa.Column(
            "id",
            sa.String(30),
            sa.ForeignKey("oauth_app_registrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_client_secret", sa.Text, nullable=False),
        sa.Column("authorize_url", sa.String(2048), nullable=False),
        sa.Column("token_url", sa.String(2048), nullable=False),
        sa.Column("default_scopes", json_type, nullable=True),
        sa.Column("secret_last_rotated_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_auth_code_app_reg_details_created_at",
        "authorization_code_app_registration_details",
        ["created_at"],
    )

    # 3. Device-flow extension — 1:1 with the base for device-flow rows.
    op.create_table(
        "device_authorization_app_registration_details",
        sa.Column(
            "id",
            sa.String(30),
            sa.ForeignKey("oauth_app_registrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("authorization_endpoint", sa.String(2048), nullable=False),
        sa.Column("token_endpoint", sa.String(2048), nullable=False),
        sa.Column("default_scopes", json_type, nullable=True),
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
        "ix_device_auth_app_reg_details_created_at",
        "device_authorization_app_registration_details",
        ["created_at"],
    )

    # 4. credentials columns — record which registration a grant was minted
    # through, and who owns the resulting connection. Adding an FK column to
    # an existing table on SQLite requires batch mode (copy-and-move).
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(30), nullable=True))
        batch.add_column(
            sa.Column(
                "oauth_app_registration_id",
                sa.String(30),
                sa.ForeignKey(
                    "oauth_app_registrations.id",
                    ondelete="RESTRICT",
                    name="fk_credentials_oauth_app_registration_id",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "ix_credentials_owner_user_id",
        "credentials",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_credentials_oauth_app_registration_id",
        "credentials",
        ["oauth_app_registration_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_credentials_oauth_app_registration_id", table_name="credentials")
    op.drop_index("ix_credentials_owner_user_id", table_name="credentials")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("oauth_app_registration_id")
        batch.drop_column("owner_user_id")

    op.drop_index(
        "ix_device_auth_app_reg_details_created_at",
        table_name="device_authorization_app_registration_details",
    )
    op.drop_table("device_authorization_app_registration_details")

    op.drop_index(
        "ix_auth_code_app_reg_details_created_at",
        table_name="authorization_code_app_registration_details",
    )
    op.drop_table("authorization_code_app_registration_details")

    op.drop_index(
        "ix_oauth_app_registrations_created_by",
        table_name="oauth_app_registrations",
    )
    op.drop_index(
        "ix_oauth_app_registrations_created_at",
        table_name="oauth_app_registrations",
    )
    op.drop_index(
        "ix_oauth_app_registrations_flow_kind",
        table_name="oauth_app_registrations",
    )
    op.drop_index(
        "ix_oauth_app_registrations_api_vendor",
        table_name="oauth_app_registrations",
    )
    op.drop_table("oauth_app_registrations")
