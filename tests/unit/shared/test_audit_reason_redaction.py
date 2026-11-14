"""Unit tests for audit reason redaction."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Connection, Table, select
from sqlalchemy.schema import CreateTable

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.shared.audit import (
    AuditAction,
    AuditTargetType,
    record_audit,
    record_audit_best_effort,
)
from jentic_one.shared.config import AppConfig, DatabaseConfig, DatabasesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.redaction import REDACTED


def _create_sqlite_tables(sync_conn: Connection, *tables: Table) -> None:
    """Create ORM tables on SQLite, temporarily stripping Postgres-only server defaults."""
    for table in tables:
        saved = {col: col.server_default for col in table.columns}
        for col in table.columns:
            col.server_default = None
        try:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
        finally:
            for col, default in saved.items():
                col.server_default = default


@pytest.fixture()
async def audit_ctx(tmp_path: Path) -> AsyncGenerator[Context, None]:
    cfg = AppConfig(
        databases=DatabasesConfig(
            admin=DatabaseConfig(backend="sqlite", path=str(tmp_path / "admin.db")),
            control=DatabaseConfig(backend="sqlite", path=str(tmp_path / "control.db")),
            registry=DatabaseConfig(backend="sqlite", path=str(tmp_path / "registry.db")),
        )
    )
    ctx = Context(cfg, allowed_dbs={"admin"}, refresh_providers_on_boot=False)
    await ctx.startup()
    async with ctx.admin_db.engine.begin() as conn:
        await conn.run_sync(_create_sqlite_tables, cast(Table, AuditEntry.__table__))
    try:
        yield ctx
    finally:
        await ctx.shutdown()


@pytest.mark.asyncio()
async def test_record_audit_masks_reason_and_preserves_before_after_keys(
    audit_ctx: Context,
) -> None:
    bearer_secret = "sk-audit-bearer-secret-123"  # pragma: allowlist secret
    token_secret = "tok-audit-query-secret-456"  # pragma: allowlist secret

    async with audit_ctx.admin_db.transaction() as session:
        await record_audit(
            session,
            action=AuditAction.REVOKE,
            target_type=AuditTargetType.SESSION,
            target_id="cs_audit_test_1",
            actor_type="user",
            actor_id="usr_audit_1",
            before={"has_session_token": True},
            after={"oauth_token_dropped": True},
            reason=f"failed with Bearer {bearer_secret} and access_token={token_secret}",
        )

    async with audit_ctx.admin_db.session() as session:
        entry = (
            await session.execute(
                select(AuditEntry).where(AuditEntry.target_id == "cs_audit_test_1")
            )
        ).scalar_one()

    assert entry.reason is not None
    assert bearer_secret not in entry.reason
    assert token_secret not in entry.reason
    assert REDACTED in entry.reason
    assert entry.before == {"has_session_token": True}
    assert entry.after == {"oauth_token_dropped": True}


@pytest.mark.asyncio()
async def test_record_audit_best_effort_masks_reason(audit_ctx: Context) -> None:
    secret = "tok-best-effort-secret-789"  # pragma: allowlist secret

    await record_audit_best_effort(
        audit_ctx,
        action=AuditAction.REVOKE,
        target_type=AuditTargetType.SESSION,
        target_id="cs_audit_test_2",
        actor_type="user",
        actor_id="usr_audit_1",
        after={"oauth_token_dropped": True},
        reason=f"callback error: access_token={secret}",
    )

    async with audit_ctx.admin_db.session() as session:
        entry = (
            await session.execute(
                select(AuditEntry).where(AuditEntry.target_id == "cs_audit_test_2")
            )
        ).scalar_one()

    assert entry.reason is not None
    assert secret not in entry.reason
    assert REDACTED in entry.reason
    assert entry.after == {"oauth_token_dropped": True}
