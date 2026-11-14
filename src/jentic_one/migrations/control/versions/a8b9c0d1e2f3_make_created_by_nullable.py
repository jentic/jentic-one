"""make created_by nullable (temporary)

Temporary migration: relaxes ``created_by`` to nullable on all control tables
so inserts succeed before the repo/service layers are updated to populate it.
Remove this migration (and re-tighten to NOT NULL) once callers set created_by.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "credentials",
    "basic_credentials",
    "token_value_credentials",
    "oauth_tokens",
    "oauth_client_credentials",
    "customer_api_keys",
    "connect_state_nonces",
    "toolkits",
    "toolkit_keys",
    "toolkit_credential_bindings",
    "toolkit_permission_rules",
)


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        op.alter_column(
            "credentials",
            "created_by",
            existing_type=sa.String(30),
            type_=sa.String(255),
            nullable=True,
        )
    else:
        with op.batch_alter_table("credentials") as batch:
            batch.alter_column(
                "created_by",
                existing_type=sa.String(30),
                type_=sa.String(255),
                nullable=True,
            )
    for table in _TABLES:
        if table == "credentials":
            continue
        if pg:
            op.alter_column(table, "created_by", existing_type=sa.String(255), nullable=True)
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column("created_by", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    for table in _TABLES:
        if pg:
            op.alter_column(table, "created_by", existing_type=sa.String(255), nullable=False)
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column("created_by", existing_type=sa.String(255), nullable=False)
