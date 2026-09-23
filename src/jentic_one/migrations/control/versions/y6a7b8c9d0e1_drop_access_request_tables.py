"""drop access_request tables (theme 7)

Theme 7 removes the access-request feature end to end: the ``/access-requests``
REST surface, service, repos, and ORM models are gone, so the backing tables
go with them. Rows are not archived — the epic (jentic/jentic-one#1372)
retires the workflow outright; historical *events* and *audit* rows referencing
access requests survive in their own stores and stay readable.

Child table first (``access_request_items`` carries the FK to
``access_requests``), then the parent. ``downgrade()`` recreates both tables at
their final schema shape — the original creation (a2b3c4d5e6f7) plus the
``rule_set_id`` column added by the theme-5 governance collapse
(s0a1b2c3d4e5) — but cannot restore dropped rows.

Revision ID: y6a7b8c9d0e1
Revises: x5f6a7b8c9d0
Create Date: 2026-09-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y6a7b8c9d0e1"  # pragma: allowlist secret
down_revision: str | None = "x5f6a7b8c9d0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"

    # Child first: access_request_items FKs access_requests (ondelete=CASCADE).
    if pg:
        op.drop_index("uq_access_request_items_pending_dedup", table_name="access_request_items")
    op.drop_index("ix_access_request_items_created_by", table_name="access_request_items")
    op.drop_index("ix_access_request_items_created_at", table_name="access_request_items")
    op.drop_index("ix_access_request_items_request_id", table_name="access_request_items")
    op.drop_table("access_request_items")

    op.drop_index("ix_access_requests_filer_owner_id", table_name="access_requests")
    op.drop_index("ix_access_requests_created_by", table_name="access_requests")
    op.drop_index("ix_access_requests_created_at", table_name="access_requests")
    op.drop_index("ix_access_requests_status", table_name="access_requests")
    op.drop_index("ix_access_requests_actor_id", table_name="access_requests")
    op.drop_table("access_requests")


def downgrade() -> None:
    """Recreate both tables (final schema shape); dropped rows are gone."""
    pg = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "access_requests",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("areq") if pg else None,
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("approve_url", sa.Text, nullable=False),
        sa.Column(
            "filed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filer_owner_id", sa.String(30), nullable=True),
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
    op.create_index("ix_access_requests_actor_id", "access_requests", ["actor_id"])
    op.create_index("ix_access_requests_status", "access_requests", ["status"])
    op.create_index("ix_access_requests_created_at", "access_requests", ["created_at"])
    op.create_index("ix_access_requests_created_by", "access_requests", ["created_by"])
    op.create_index("ix_access_requests_filer_owner_id", "access_requests", ["filer_owner_id"])

    op.create_table(
        "access_request_items",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("arqi") if pg else None,
            nullable=False,
        ),
        sa.Column("access_request_id", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.String(30), nullable=False),
        sa.Column("resource_type", sa.String(30), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column(
            "resource_reference",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("to_type", sa.String(30), nullable=True),
        sa.Column("to_id", sa.String(255), nullable=True),
        sa.Column(
            "rules",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("rule_set_id", sa.String(30), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column(
            "applied_effects",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text, nullable=True),
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
        sa.ForeignKeyConstraint(["access_request_id"], ["access_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_access_request_items_request_id",
        "access_request_items",
        ["access_request_id"],
    )
    op.create_index("ix_access_request_items_created_at", "access_request_items", ["created_at"])
    op.create_index("ix_access_request_items_created_by", "access_request_items", ["created_by"])

    if pg:
        op.create_index(
            "uq_access_request_items_pending_dedup",
            "access_request_items",
            ["actor_id", "resource_type", "action", "to_id", "resource_id"],
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        )
