"""add oauth client public-client and approval columns

Phase-3a-1 (design §4.1, D5/D6/D7): public secret-less OAuth clients and the
admin approval lifecycle.

- ``client_secret_hash`` becomes nullable — public clients store NULL.
- ``token_endpoint_auth_method`` (default ``client_secret_basic``; ``none``
  for public clients).
- ``consent_model`` (``user``|``agent``, default ``user``).
- ``registration_source`` (``admin``|``dcr``, default ``admin``).
- ``software_id`` (nullable) — RFC 7591 software identity, dedupe key input.
- ``approval_status`` (``pending``|``approved``|``denied``) — server default
  ``approved`` so every existing (admin-created) row stays live on upgrade.

Additive only; batch_alter_table so the nullability change also works on the
SQLite integration backend.

Revision ID: b7c8d9e0f1a2
Revises: f2a3b4c5d6e7
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("oauth_clients") as batch_op:
        batch_op.alter_column("client_secret_hash", existing_type=sa.String(128), nullable=True)
        batch_op.add_column(
            sa.Column(
                "token_endpoint_auth_method",
                sa.String(32),
                nullable=False,
                server_default="client_secret_basic",
            )
        )
        batch_op.add_column(
            sa.Column("consent_model", sa.String(8), nullable=False, server_default="user")
        )
        batch_op.add_column(
            sa.Column("registration_source", sa.String(8), nullable=False, server_default="admin")
        )
        batch_op.add_column(sa.Column("software_id", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("approval_status", sa.String(8), nullable=False, server_default="approved")
        )
    op.create_index("ix_oauth_clients_approval_status", "oauth_clients", ["approval_status"])


def downgrade() -> None:
    op.drop_index("ix_oauth_clients_approval_status", table_name="oauth_clients")
    # Public (secret-less) rows cannot exist under the NOT NULL secret schema;
    # dropping them mirrors the destructive-downgrade precedent of aa6b7c8d9e0f.
    op.execute("DELETE FROM oauth_clients WHERE client_secret_hash IS NULL")
    with op.batch_alter_table("oauth_clients") as batch_op:
        batch_op.drop_column("approval_status")
        batch_op.drop_column("software_id")
        batch_op.drop_column("registration_source")
        batch_op.drop_column("consent_model")
        batch_op.drop_column("token_endpoint_auth_method")
        batch_op.alter_column("client_secret_hash", existing_type=sa.String(128), nullable=False)
