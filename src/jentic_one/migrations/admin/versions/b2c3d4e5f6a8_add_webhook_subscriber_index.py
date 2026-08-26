"""add webhook subscriber (GIN) and per-endpoint claim indexes

Two indexes supporting Phase 1 hardening of the outbound-webhook pipeline:

``ix_webhook_endpoints_event_types_gin``
    A **GIN** index on the JSONB ``event_types`` column so the subscriber lookup
    (``event_types @> '["x"]'``) runs as an index scan instead of a full-table
    scan on every relayed event. GIN is Postgres-only; on SQLite the JSON column
    has no containment operator and the subscriber query takes its portable
    Python-filter branch, so the index is simply skipped there.

``ix_webhook_deliveries_endpoint_next_attempt``
    Serves the per-endpoint send-concurrency cap: ``claim_due`` does a
    ``DISTINCT ON (endpoint_id) … ORDER BY endpoint_id, next_attempt_at`` to pick
    the earliest-due row per endpoint, which this composite index answers
    directly. Portable (plain btree) across both dialects.

Both are index-only — no table/column changes, no data migration.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-26

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a8"  # pragma: allowlist secret
down_revision: str | None = "a1b2c3d4e5f7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"

    # GIN containment index — Postgres only. SQLite's ``event_types`` is plain
    # JSON with no ``@>`` operator, so a GIN index is meaningless (and
    # unsupported) there; the subscriber query uses its Python fallback instead.
    if pg:
        op.create_index(
            "ix_webhook_endpoints_event_types_gin",
            "webhook_endpoints",
            ["event_types"],
            postgresql_using="gin",
        )

    op.create_index(
        "ix_webhook_deliveries_endpoint_next_attempt",
        "webhook_deliveries",
        ["endpoint_id", "next_attempt_at"],
    )


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"

    op.drop_index(
        "ix_webhook_deliveries_endpoint_next_attempt",
        table_name="webhook_deliveries",
    )
    if pg:
        op.drop_index(
            "ix_webhook_endpoints_event_types_gin",
            table_name="webhook_endpoints",
        )
