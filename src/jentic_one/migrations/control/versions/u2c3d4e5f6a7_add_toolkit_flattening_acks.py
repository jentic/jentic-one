"""add toolkit_flattening_acks

Theme-5 Phase 6a: the acknowledgement sentinel gating the Phase-6b drops.
One row per acknowledged verification run of the flattening job, written
only by ``jentic_one flatten-toolkits --verify --acknowledge`` when the
verification passed in that same invocation. Phase 6b's drop migrations
must count this table and **raise** when it is empty (guard-and-raise) —
dropping the toolkit tables with no acknowledged flatten on record risks a
total default-deny authorization outage for every legacy-bound agent.

Revision ID: u2c3d4e5f6a7
Revises: t1b2c3d4e5f6
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u2c3d4e5f6a7"  # pragma: allowlist secret
down_revision: str | None = "t1b2c3d4e5f6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "toolkit_flattening_acks",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function, so inserts there must go through the ORM model, whose
            # Python-side default (generate_ksuid("tfa")) supplies the id.
            server_default=sa.func.generate_ksuid("tfa") if pg else None,
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legacy_pair_count", sa.Integer, nullable=False),
        sa.Column("direct_binding_count", sa.Integer, nullable=False),
        sa.Column("report_finding_count", sa.Integer, nullable=False),
        sa.Column("tool_version", sa.String(50), nullable=False),
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
        "ix_toolkit_flattening_acks_created_at", "toolkit_flattening_acks", ["created_at"]
    )
    op.create_index(
        "ix_toolkit_flattening_acks_created_by", "toolkit_flattening_acks", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_toolkit_flattening_acks_created_by", table_name="toolkit_flattening_acks")
    op.drop_index("ix_toolkit_flattening_acks_created_at", table_name="toolkit_flattening_acks")
    op.drop_table("toolkit_flattening_acks")
