"""add webhook duration_ms, allowed_cidrs and delivery-attempt history

Phase 3 of the outbound-webhook hardening. Three related changes in one
revision, all in the ``admin`` schema:

``webhook_deliveries.duration_ms``
    Nullable ``Integer`` holding the wall-clock duration (ms) of the most recent
    HTTP attempt, populated by ``delivery._send``. Nullable with no backfill —
    existing rows simply have no recorded duration until their next attempt,
    which is correct (we never measured them).

``webhook_endpoints.allowed_cidrs``
    A per-endpoint IP/CIDR allowlist (JSONB on Postgres, JSON on SQLite),
    defaulting to an empty array. Mirrors the ``event_types`` column exactly:
    non-nullable with a ``'[]'`` server default so existing rows get an empty
    allowlist (no per-endpoint exemption — only the operator-wide egress policy
    applies), and no GIN index (the list is read per-send by primary key, never
    searched).

``webhook_delivery_attempts`` (new child table)
    One append-only row per real outbound attempt: the durable per-attempt
    history the drawer's response-time view and attempt timeline read. Same-
    schema ``CASCADE`` FK to ``webhook_deliveries`` so pruning a delivery (or
    deleting its endpoint) reaps its history too. Carries no response body, same
    as the parent.

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-08-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b9"  # pragma: allowlist secret
down_revision: str | None = "b2c3d4e5f6a8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    json_type = sa.dialects.postgresql.JSONB() if pg else sa.JSON()

    # Response-time column on the parent delivery (nullable; no backfill).
    op.add_column(
        "webhook_deliveries",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )

    # Per-endpoint allowlist — same shape/default as ``event_types``.
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "allowed_cidrs",
            json_type,
            server_default=sa.text("'[]'::jsonb") if pg else sa.text("'[]'"),
            nullable=False,
        ),
    )

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("whda") if pg else None,
            nullable=False,
        ),
        sa.Column("delivery_id", sa.String(30), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["delivery_id"], ["webhook_deliveries.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_webhook_delivery_attempts_delivery_created",
        "webhook_delivery_attempts",
        ["delivery_id", "created_at"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_created_at",
        "webhook_delivery_attempts",
        ["created_at"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_created_by",
        "webhook_delivery_attempts",
        ["created_by"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_delivery_attempts_created_by",
        table_name="webhook_delivery_attempts",
    )
    op.drop_index(
        "ix_webhook_delivery_attempts_created_at",
        table_name="webhook_delivery_attempts",
    )
    op.drop_index(
        "ix_webhook_delivery_attempts_delivery_created",
        table_name="webhook_delivery_attempts",
    )
    op.drop_table("webhook_delivery_attempts")

    op.drop_column("webhook_endpoints", "allowed_cidrs")
    op.drop_column("webhook_deliveries", "duration_ms")
