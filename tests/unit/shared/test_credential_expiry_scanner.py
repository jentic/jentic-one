"""Unit tests for the credential-expiry scanner.

Drives ``CredentialExpiryScanner.sweep()`` against real in-memory SQLite control
+ admin DBs (no DB mocking — ``tests/arch/test_no_db_mocking.py``). The control
DB holds ``oauth_tokens`` + ``credentials``; the admin DB holds ``events``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.credential_expiry_scanner import CredentialExpiryScanner
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventSeverity, EventType


def _create_tables(sync_conn: Connection, *tables: Table) -> None:
    """Create the given tables on SQLite, stripping Postgres-only server defaults."""
    for table in tables:
        saved = {col: col.server_default for col in table.columns}
        for col in table.columns:
            col.server_default = None
        try:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
        finally:
            for col, default in saved.items():
                col.server_default = default


class _MemoryDb(DatabaseSession):
    """A DatabaseSession over a shared in-memory SQLite engine."""

    def __init__(self) -> None:
        super().__init__(DatabaseConfig(backend="sqlite", path=":memory:"))
        self._mem_engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._engine = self._mem_engine
        self._session_factory = async_sessionmaker(bind=self._mem_engine, expire_on_commit=False)


@pytest.fixture()
async def control_db() -> AsyncGenerator[_MemoryDb, None]:
    db = _MemoryDb()
    tables = (cast(Table, Credential.__table__), cast(Table, OAuthToken.__table__))
    async with db.engine.begin() as conn:
        await conn.run_sync(_create_tables, *tables)
    yield db
    await db.engine.dispose()


@pytest.fixture()
async def admin_db() -> AsyncGenerator[_MemoryDb, None]:
    db = _MemoryDb()
    async with db.engine.begin() as conn:
        await conn.run_sync(_create_tables, cast(Table, Event.__table__))
    yield db
    await db.engine.dispose()


async def _add_token(
    control_db: _MemoryDb,
    *,
    credential_id: str,
    expires_at: datetime | None,
    revoked: bool = False,
    api_vendor: str = "stripe",
) -> None:
    async with control_db.transaction() as session:
        session.add(
            Credential(
                id=credential_id,
                type="oauth2",
                name=f"cred-{credential_id}",
                api_vendor=api_vendor,
                provider="direct_oauth2",
            )
        )
        await session.flush()
        session.add(
            OAuthToken(
                id=f"oat_{credential_id}",
                credential_id=credential_id,
                encrypted_access_token="enc:secret-token-material",
                expires_at=expires_at,
                revoked_at=datetime.now(UTC) if revoked else None,
                created_by="usr_test",
            )
        )


async def _events(admin_db: _MemoryDb, event_type: str) -> list[Event]:
    async with admin_db.session() as session:
        return await EventRepository.list_all(session, event_type=[event_type])


async def _marker(control_db: _MemoryDb, credential_id: str) -> OAuthToken:
    async with control_db.session() as session:
        token = await session.get(OAuthToken, f"oat_{credential_id}")
        assert token is not None
        return token


async def test_expiring_soon_emits_warning_and_stamps_marker(
    control_db: _MemoryDb, admin_db: _MemoryDb
) -> None:
    soon = datetime.now(UTC) + timedelta(hours=24)
    await _add_token(control_db, credential_id="cred_soon", expires_at=soon)

    scanner = CredentialExpiryScanner(control_db, admin_db)
    emitted = await scanner.sweep()

    assert emitted == 1
    events = await _events(admin_db, EventType.CREDENTIAL_EXPIRING_SOON)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.WARNING.value
    assert events[0].requires_action is False
    assert events[0].data["credential_id"] == "cred_soon"
    assert events[0].data["api_vendor"] == "stripe"
    # Cross-check against the documented severity matrix (issue #907).
    assert (
        EventSeverity(events[0].severity)
        in EVENT_TYPE_SEVERITIES[EventType.CREDENTIAL_EXPIRING_SOON]
    )

    token = await _marker(control_db, "cred_soon")
    assert token.expiring_soon_event_at is not None
    assert token.expired_event_at is None


async def test_expired_emits_error_and_requires_action(
    control_db: _MemoryDb, admin_db: _MemoryDb
) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    await _add_token(control_db, credential_id="cred_dead", expires_at=past)

    scanner = CredentialExpiryScanner(control_db, admin_db)
    emitted = await scanner.sweep()

    assert emitted == 1
    events = await _events(admin_db, EventType.CREDENTIAL_EXPIRED)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.ERROR.value
    assert events[0].requires_action is True
    # Cross-check against the documented severity matrix (issue #907).
    assert EventSeverity(events[0].severity) in EVENT_TYPE_SEVERITIES[EventType.CREDENTIAL_EXPIRED]

    token = await _marker(control_db, "cred_dead")
    assert token.expired_event_at is not None


async def test_second_sweep_is_a_noop(control_db: _MemoryDb, admin_db: _MemoryDb) -> None:
    await _add_token(
        control_db,
        credential_id="cred_soon",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    scanner = CredentialExpiryScanner(control_db, admin_db)

    assert await scanner.sweep() == 1
    assert await scanner.sweep() == 0
    assert len(await _events(admin_db, EventType.CREDENTIAL_EXPIRING_SOON)) == 1


async def test_revoked_tokens_skipped(control_db: _MemoryDb, admin_db: _MemoryDb) -> None:
    await _add_token(
        control_db,
        credential_id="cred_revoked",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        revoked=True,
    )
    scanner = CredentialExpiryScanner(control_db, admin_db)

    assert await scanner.sweep() == 0
    assert await _events(admin_db, EventType.CREDENTIAL_EXPIRED) == []


async def test_tokens_without_expiry_skipped(control_db: _MemoryDb, admin_db: _MemoryDb) -> None:
    await _add_token(control_db, credential_id="cred_noexp", expires_at=None)
    scanner = CredentialExpiryScanner(control_db, admin_db)

    assert await scanner.sweep() == 0
    assert await _events(admin_db, EventType.CREDENTIAL_EXPIRING_SOON) == []
    assert await _events(admin_db, EventType.CREDENTIAL_EXPIRED) == []


async def test_tokens_beyond_window_skipped(control_db: _MemoryDb, admin_db: _MemoryDb) -> None:
    far = datetime.now(UTC) + timedelta(days=30)
    await _add_token(control_db, credential_id="cred_far", expires_at=far)
    scanner = CredentialExpiryScanner(control_db, admin_db)

    assert await scanner.sweep() == 0


async def test_event_carries_no_token_material(control_db: _MemoryDb, admin_db: _MemoryDb) -> None:
    await _add_token(
        control_db,
        credential_id="cred_soon",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    scanner = CredentialExpiryScanner(control_db, admin_db)
    await scanner.sweep()

    events = await _events(admin_db, EventType.CREDENTIAL_EXPIRING_SOON)
    serialized = f"{events[0].summary} {events[0].detail} {events[0].data}"
    assert "enc:secret-token-material" not in serialized
