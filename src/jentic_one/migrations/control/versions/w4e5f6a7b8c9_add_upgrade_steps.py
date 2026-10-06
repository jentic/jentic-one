"""add upgrade_steps

The ledger of one-shot post-migration data steps. The migration runner
(``python -m jentic_one.migrations.run``) performs cross-database data steps
(the theme-5 toolkit flattening reads the control DB and writes the admin DB,
so it cannot live in one Alembic tree) after every tree reaches head, and
records each completed step here by name. The unique name is what makes a
step run at most once per install, including under concurrent runners.

Schema only — the rows are written by the runner, never by a migration.

Revision ID: w4e5f6a7b8c9
Revises: u2c3d4e5f6a7
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w4e5f6a7b8c9"  # pragma: allowlist secret
down_revision: str | None = "u2c3d4e5f6a7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "upgrade_steps",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function, so inserts there go through the ORM model, whose
            # Python-side default (generate_ksuid("ups")) supplies the id.
            server_default=sa.func.generate_ksuid("ups") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tool_version", sa.String(50), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
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
        sa.UniqueConstraint("name", name="uq_upgrade_steps_name"),
    )
    op.create_index("ix_upgrade_steps_created_at", "upgrade_steps", ["created_at"])
    op.create_index("ix_upgrade_steps_created_by", "upgrade_steps", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_upgrade_steps_created_by", table_name="upgrade_steps")
    op.drop_index("ix_upgrade_steps_created_at", table_name="upgrade_steps")
    op.drop_table("upgrade_steps")
