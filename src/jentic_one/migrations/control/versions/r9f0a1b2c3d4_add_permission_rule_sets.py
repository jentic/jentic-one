"""add permission_rule_sets

Shared, reusable ordered permission-rule lists (theme 5, phase 0 — rule
grouping, Q-04). ``permission_rule_sets`` names one list;
``permission_rule_set_rules`` holds its ordered rows (same rule columns and
default-deny/first-match-wins semantics as ``agent_permission_rules``,
keyed by ``rule_set_id``). N ``agent_credential_bindings`` rows (admin DB)
point at a set via their FK-less ``rule_set_id``; NULL there means the
binding's inline rules apply. ``UNIQUE (rule_set_id, sequence)`` guards
evaluation-order determinism, mirroring
``uq_agent_permission_rules_binding_seq``.

Revision ID: r9f0a1b2c3d4
Revises: q8e9f0a1b2c3
Create Date: 2026-09-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r9f0a1b2c3d4"
down_revision: str | None = "q8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "permission_rule_sets",
        sa.Column(
            "id",
            sa.String(30),
            # Postgres generates the ksuid server-side; SQLite has no such
            # function, so inserts there must go through the ORM model, whose
            # Python-side default (generate_ksuid("prs")) supplies the id.
            server_default=sa.func.generate_ksuid("prs") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
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
        sa.UniqueConstraint("name", name="uq_permission_rule_sets_name"),
    )
    op.create_index("ix_permission_rule_sets_created_at", "permission_rule_sets", ["created_at"])
    op.create_index("ix_permission_rule_sets_created_by", "permission_rule_sets", ["created_by"])

    op.create_table(
        "permission_rule_set_rules",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("prr") if pg else None,
            nullable=False,
        ),
        sa.Column("rule_set_id", sa.String(30), nullable=False),
        sa.Column("effect", sa.String(10), nullable=False),
        sa.Column(
            "methods",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("path", sa.String(1000), nullable=True),
        sa.Column(
            "match_mode",
            sa.String(10),
            server_default=sa.text("'regex'"),
            nullable=False,
        ),
        sa.Column(
            "operations",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("is_system", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("sequence", sa.Integer, nullable=False),
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
        sa.ForeignKeyConstraint(["rule_set_id"], ["permission_rule_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_set_id", "sequence", name="uq_permission_rule_set_rules_seq"),
    )
    op.create_index(
        "ix_permission_rule_set_rules_created_at", "permission_rule_set_rules", ["created_at"]
    )
    op.create_index(
        "ix_permission_rule_set_rules_created_by", "permission_rule_set_rules", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_permission_rule_set_rules_created_by", table_name="permission_rule_set_rules")
    op.drop_index("ix_permission_rule_set_rules_created_at", table_name="permission_rule_set_rules")
    op.drop_table("permission_rule_set_rules")
    op.drop_index("ix_permission_rule_sets_created_by", table_name="permission_rule_sets")
    op.drop_index("ix_permission_rule_sets_created_at", table_name="permission_rule_sets")
    op.drop_table("permission_rule_sets")
