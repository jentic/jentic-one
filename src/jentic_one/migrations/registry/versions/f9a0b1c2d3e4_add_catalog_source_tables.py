"""add catalog source tables

Adds the multi-source catalog storage: per-source sync state
(``catalog_sources``), the entries every source lists (``catalog_entries``), the
per-run staging area (``catalog_staged_entries``) and the sync run log
(``catalog_sync_runs``). ``catalog_snapshots`` stays in place and keeps serving
reads; these tables are filled alongside it.

The downgrade drops the four tables. It does not touch ``catalog_snapshots``.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-17

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9a0b1c2d3e4"  # pragma: allowlist secret
down_revision: str | None = "e8f9a0b1c2d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[Any]:
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _ksuid_id(prefix: str, pg: bool) -> sa.Column[Any]:
    return sa.Column(
        "id",
        sa.String(length=30),
        server_default=sa.text(f"generate_ksuid('{prefix}')") if pg else None,
        nullable=False,
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    true_default = sa.text("true") if pg else sa.text("1")
    false_default = sa.text("false") if pg else sa.text("0")

    op.create_table(
        "catalog_sources",
        _ksuid_id("csrc", pg),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=true_default, nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("trust", sa.String(length=16), nullable=False),
        sa.Column("precedence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("watermark", sa.Text(), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("listing_digest", sa.String(length=64), nullable=True),
        sa.Column("live_entries", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_applied_run_id", sa.String(length=30), nullable=True),
        sa.Column(
            "last_status", sa.String(length=16), server_default="never_synced", nullable=False
        ),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("sync_lease_owner", sa.String(length=30), nullable=True),
        sa.Column("sync_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_wanted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reresolve_wanted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolver_inputs_digest", sa.String(length=64), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_catalog_sources_slug"),
    )

    op.create_table(
        "catalog_entries",
        _ksuid_id("cent", pg),
        sa.Column("entry_id", sa.String(length=320), nullable=False),
        sa.Column("source_slug", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=287), nullable=False),
        sa.Column("catalog_api_id_hint", sa.String(length=512), nullable=True),
        sa.Column("catalog_api_id", sa.String(length=320), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("spec_ref", _json(), nullable=True),
        sa.Column("human_url", sa.String(length=2048), nullable=True),
        sa.Column("tags", _json(), nullable=False),
        sa.Column("content_hint", sa.Text(), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", name="uq_catalog_entries_entry_id"),
        sa.UniqueConstraint(
            "source_slug", "external_id", name="uq_catalog_entries_source_slug_external_id"
        ),
    )
    op.create_index("ix_catalog_entries_catalog_api_id", "catalog_entries", ["catalog_api_id"])
    op.create_index(
        "ix_catalog_entries_source_slug_removed_at",
        "catalog_entries",
        ["source_slug", "removed_at"],
    )

    op.create_table(
        "catalog_staged_entries",
        _ksuid_id("cstg", pg),
        sa.Column("sync_run_id", sa.String(length=30), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=287), nullable=False),
        sa.Column("catalog_api_id_hint", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("spec_ref", _json(), nullable=True),
        sa.Column("human_url", sa.Text(), nullable=True),
        sa.Column("tags", _json(), nullable=False),
        sa.Column("content_hint", sa.Text(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sync_run_id", "external_id", name="uq_catalog_staged_entries_run_external_id"
        ),
    )

    op.create_table(
        "catalog_sync_runs",
        _ksuid_id("csyn", pg),
        sa.Column("source_slug", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_full", sa.Boolean(), nullable=True),
        sa.Column("forced", sa.Boolean(), server_default=false_default, nullable=False),
        sa.Column("staged", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("upserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("removed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_sync_runs_source_slug_started_at",
        "catalog_sync_runs",
        ["source_slug", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_sync_runs_source_slug_started_at", table_name="catalog_sync_runs")
    op.drop_table("catalog_sync_runs")
    op.drop_table("catalog_staged_entries")
    op.drop_index("ix_catalog_entries_source_slug_removed_at", table_name="catalog_entries")
    op.drop_index("ix_catalog_entries_catalog_api_id", table_name="catalog_entries")
    op.drop_table("catalog_entries")
    op.drop_table("catalog_sources")
