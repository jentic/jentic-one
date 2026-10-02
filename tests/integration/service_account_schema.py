"""Test-only restore of the pre-drop admin schema (theme-8 Phase 4).

The admin drop ``e2f3a4b5c6d7`` is irreversible: its ``downgrade()`` raises,
because recreating empty service-account tables would restore nothing (the
operator's way back is the pre-upgrade snapshot). Tests that exercise the
pre-drop state — the retirement service, the drop gate, the theme-5 6b
rollback drills that walk admin further down — model that snapshot restore
instead: recreate the three tables in their final historical shape and stamp
admin back to ``d1e2f3a4b5c6``. From there the chain is walkable again.
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, pool
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.db.session import get_database_url
from tests.integration.conftest import _alembic_config_for

#: The revision just below the drop, and the drop itself.
ADMIN_PRE_SA_DROP = "d1e2f3a4b5c6"  # pragma: allowlist secret
_SA_DROP = "e2f3a4b5c6d7"  # pragma: allowlist secret


def _create_tables(op: Operations, *, pg: bool) -> None:
    """The three tables as they stood at ``d1e2f3a4b5c6`` (Phase-1 shape)."""
    op.create_table(
        "service_accounts",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("sva") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column(
            "owner_id",
            sa.String(30),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("registered_by", sa.String(30), nullable=False),
        sa.Column(
            "approved_by",
            sa.String(30),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("denial_reason", sa.String(1024), nullable=True),
        sa.Column("denied_by", sa.String(30), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migrated_to_actor_id", sa.String(30), nullable=True),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("owner_id", "status", "created_at", "created_by", "migrated_to_actor_id"):
        op.create_index(f"ix_service_accounts_{column}", "service_accounts", [column])

    op.create_table(
        "service_account_credentials",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column(
            "service_account_id",
            sa.String(30),
            sa.ForeignKey("service_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_secret_hash", sa.String(128), nullable=True),
        sa.Column("api_key_hash", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sa_credentials_service_account_id",
        "service_account_credentials",
        ["service_account_id"],
        unique=True,
    )
    for column in ("created_at", "created_by"):
        op.create_index(
            f"ix_service_account_credentials_{column}", "service_account_credentials", [column]
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
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("created_at", "created_by"):
        op.create_index(
            f"ix_service_account_migration_acks_{column}",
            "service_account_migration_acks",
            [column],
        )


async def _restore_tables(db_config: DatabaseConfig) -> bool:
    """Create the tables unless present; returns whether it created them."""
    pg = db_config.backend == "postgres"
    connect_args = (
        {"server_settings": {"search_path": f"{db_config.schema_name},public"}} if pg else {}
    )
    engine = create_async_engine(
        get_database_url(db_config), poolclass=pool.NullPool, connect_args=connect_args
    )

    def _run(sync_conn: sa.Connection) -> bool:
        if inspect(sync_conn).has_table("service_accounts"):
            return False
        _create_tables(Operations(MigrationContext.configure(sync_conn)), pg=pg)
        return True

    try:
        async with engine.begin() as conn:
            return await conn.run_sync(_run)
    finally:
        await engine.dispose()


def restore_pre_sa_drop_admin(integration_config: AppConfig) -> None:
    """Bring admin from head back to ``d1e2f3a4b5c6`` the snapshot way.

    A no-op when the service-account tables already exist (admin is already
    below the drop). Blocking — call it via ``asyncio.to_thread`` from async
    code.
    """
    db_config = integration_config.databases.admin
    cfg = _alembic_config_for("admin", db_config)
    heads = ScriptDirectory.from_config(cfg).get_heads()
    # Stamping skips downgrades; only safe while the drop is admin's head.
    assert heads == [_SA_DROP], f"extend restore_pre_sa_drop_admin for new heads {heads}"
    if asyncio.run(_restore_tables(db_config)):
        command.stamp(cfg, ADMIN_PRE_SA_DROP)
