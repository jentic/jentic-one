"""add SA-migration stamp, sentinel, and api_key_hash unique partial index

Theme-8 Phase 1 trio (plan: M-2 / M-B / H-A x F6), one additive migration:

1. ``service_accounts.migrated_to_actor_id`` + ``migrated_at`` — the
   migration job's idempotency stamp (successor ``agnt_`` id or the literal
   ``skipped`` sentinel) and its timestamp (the NF-3 verify criterion and
   the N3 sweep age gate are both stamp-time-relative).
2. ``service_account_migration_acks`` — the operator-acknowledgement
   sentinel gating the Phase-4 drops (checked directly by the drop
   migration; re-verified at drop time per F5 x M-C). Admin DB, so no
   cross-DB proxy is needed.
3. ``uq_agent_credentials_api_key_hash`` — the dialect-independent
   double-mint backstop: without it, two concurrent job runs (boot replica
   + CLI are separate processes; the pg advisory lock is a documented no-op
   on SQLite) could insert two agent_credentials rows sharing one digest,
   and the resolver's .one_or_none() would 500 on every request for that
   key. Partial (NULL-exempt) with BOTH dialect kwargs.

Pre-flight note: if any two agent_credentials rows already share a non-NULL
digest (none should — ``jak_`` keys are random), the index creation fails
this migration loudly. That is correct behaviour: a shared digest is already
a live resolver 500 for that key and must be resolved by hand, never papered
over.

Batch-safe on SQLite (``render_as_batch``): pure add_column / create_table /
create_index — no table rewrite.

Revision ID: c0d1e2f3a4b5
Revises: b9d0e1f2a3b4
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0d1e2f3a4b5"  # pragma: allowlist secret
down_revision: str | None = "b9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.add_column(
        "service_accounts",
        sa.Column("migrated_to_actor_id", sa.String(30), nullable=True),
    )
    op.add_column(
        "service_accounts",
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_service_accounts_migrated_to_actor_id",
        "service_accounts",
        ["migrated_to_actor_id"],
    )
    op.create_table(
        "service_account_migration_acks",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("smak") if pg else None,
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unstamped_count", sa.Integer, nullable=False),
        sa.Column("grant_twin_missing_count", sa.Integer, nullable=False),
        sa.Column("unrevoked_token_count", sa.Integer, nullable=False),
        sa.Column("digest_mismatch_count", sa.Integer, nullable=False),
        sa.Column("post_stamp_mutation_count", sa.Integer, nullable=False),
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
        "ix_service_account_migration_acks_created_at",
        "service_account_migration_acks",
        ["created_at"],
    )
    op.create_index(
        "ix_service_account_migration_acks_created_by",
        "service_account_migration_acks",
        ["created_by"],
    )
    # The backstop (H-A x F6). BOTH dialect kwargs — Postgres and SQLite
    # each enforce the partial uniqueness natively (SQLite >= 3.8).
    op.create_index(
        "uq_agent_credentials_api_key_hash",
        "agent_credentials",
        ["api_key_hash"],
        unique=True,
        postgresql_where=sa.text("api_key_hash IS NOT NULL"),
        sqlite_where=sa.text("api_key_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_credentials_api_key_hash", table_name="agent_credentials")
    op.drop_index(
        "ix_service_account_migration_acks_created_by",
        table_name="service_account_migration_acks",
    )
    op.drop_index(
        "ix_service_account_migration_acks_created_at",
        table_name="service_account_migration_acks",
    )
    op.drop_table("service_account_migration_acks")
    op.drop_index("ix_service_accounts_migrated_to_actor_id", table_name="service_accounts")
    op.drop_column("service_accounts", "migrated_at")
    op.drop_column("service_accounts", "migrated_to_actor_id")
