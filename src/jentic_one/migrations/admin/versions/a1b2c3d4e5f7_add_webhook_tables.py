"""add webhook endpoints, events and deliveries

Three tables behind the webhook feature:

``webhook_endpoints``
    One configured outbound ``notification`` webhook per row. Holds the signing
    secret **encrypted** (AES-256-GCM envelope via ``EncryptionService``) rather
    than hashed, because HMAC needs the key itself; ``secret_hash`` is kept
    alongside only as a non-reversible fingerprint for reuse detection. The
    ``previous_secret_*`` columns allow a rotation grace period during which both
    keys are honoured, so rotating does not drop in-flight events.

``webhook_events``
    Every accepted event. The unique ``(endpoint_id, source_event_id)``
    constraint is the deduplication guarantee — senders retry, so the same
    event legitimately arrives twice and "already seen" must be a database
    fact, not application hope.

``webhook_deliveries``
    One row per (event x destination): the durable outbound queue, claimed on
    ``(status, next_attempt_at)``.

Same-schema foreign keys only (all three live in ``admin``), so the CASCADEs
here are safe — deleting an endpoint reaps its events and deliveries.

Revision ID: a1b2c3d4e5f7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"  # pragma: allowlist secret
down_revision: str | None = "e1f2a3b4c5d6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    # Postgres generates the ksuid server-side; SQLite has no such function, so
    # there is no server_default there and every insert is expected to go
    # through the ORM model, whose Python-side default supplies the id. Mirrors
    # the provider_configs migration.
    json_type = sa.dialects.postgresql.JSONB() if pg else sa.JSON()

    op.create_table(
        "webhook_endpoints",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("whep") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("secret_hash", sa.String(128), nullable=False),
        # Reversible on purpose: HMAC verification needs the key, not a digest.
        # Text (not String(n)) because the ciphertext length depends on the
        # secret length and the key-id prefix.
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        # Rotation grace: both the new and previous secret verify until the
        # expiry passes, so rotating never drops an in-flight event.
        sa.Column("previous_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("previous_secret_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_url", sa.String(2048), nullable=True),
        sa.Column(
            "event_types",
            json_type,
            server_default=sa.text("'[]'::jsonb") if pg else sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
    op.create_index("ix_webhook_endpoints_name", "webhook_endpoints", ["name"], unique=True)
    op.create_index("ix_webhook_endpoints_created_at", "webhook_endpoints", ["created_at"])
    op.create_index("ix_webhook_endpoints_created_by", "webhook_endpoints", ["created_by"])

    op.create_table(
        "webhook_events",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("whev") if pg else None,
            nullable=False,
        ),
        sa.Column("endpoint_id", sa.String(30), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "payload",
            json_type,
            server_default=sa.text("'{}'::jsonb") if pg else sa.text("'{}'"),
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
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "endpoint_id", "source_event_id", name="uq_webhook_events_endpoint_source"
        ),
    )
    op.create_index("ix_webhook_events_endpoint_id", "webhook_events", ["endpoint_id"])
    op.create_index(
        "ix_webhook_events_type_created", "webhook_events", ["event_type", "created_at"]
    )
    op.create_index("ix_webhook_events_created_at", "webhook_events", ["created_at"])
    op.create_index("ix_webhook_events_created_by", "webhook_events", ["created_by"])

    op.create_table(
        "webhook_deliveries",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("whdl") if pg else None,
            nullable=False,
        ),
        sa.Column("event_id", sa.String(30), nullable=False),
        sa.Column("endpoint_id", sa.String(30), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["event_id"], ["webhook_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_webhook_deliveries_claim",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"])
    op.create_index(
        "ix_webhook_deliveries_endpoint_created",
        "webhook_deliveries",
        ["endpoint_id", "created_at"],
    )
    op.create_index("ix_webhook_deliveries_created_at", "webhook_deliveries", ["created_at"])
    op.create_index("ix_webhook_deliveries_created_by", "webhook_deliveries", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_created_by", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_endpoint_created", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_claim", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")

    op.drop_index("ix_webhook_events_created_by", table_name="webhook_events")
    op.drop_index("ix_webhook_events_created_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_type_created", table_name="webhook_events")
    op.drop_index("ix_webhook_events_endpoint_id", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("ix_webhook_endpoints_created_by", table_name="webhook_endpoints")
    op.drop_index("ix_webhook_endpoints_created_at", table_name="webhook_endpoints")
    op.drop_index("ix_webhook_endpoints_name", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
