"""Unit tests for the idle-warning job.

Drives ``BindingIdleWarningScanner.sweep()`` against a real in-memory SQLite
admin DB (no DB mocking — ``tests/arch/test_no_db_mocking.py``) holding
``agents``, ``agent_credential_bindings`` and ``events``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos.agent_credential_binding_repo import AgentCredentialBindingRepository
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.broker.repos.binding_use_repo import BindingUseRepository
from jentic_one.shared.config import DatabaseConfig, GrantExpiryConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.binding_idle_warning_scanner import BindingIdleWarningScanner
from jentic_one.shared.models.events import EventType

_DAY = 86_400
_NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


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
async def admin_db() -> AsyncGenerator[_MemoryDb, None]:
    db = _MemoryDb()
    tables = (
        cast(Table, Agent.__table__),
        cast(Table, AgentCredentialBinding.__table__),
        cast(Table, Event.__table__),
    )
    async with db.engine.begin() as conn:
        await conn.run_sync(_create_tables, *tables)
    async with db.transaction() as session:
        session.add(
            Agent(
                id="agt_idle",
                name="Reporter",
                owner_id="usr_owner",
                registered_by="usr_owner",
                status="active",
                created_by="usr_owner",
            )
        )
    yield db
    await db.engine.dispose()


async def _bind(
    db: _MemoryDb,
    credential_id: str,
    *,
    idle_days: int,
    left: timedelta,
    warning: bool = True,
    suspended: bool = False,
    expires_at: datetime | None = None,
) -> None:
    """A binding with an idle limit of ``idle_days`` that ends in ``left`` if unused."""
    renewed = _NOW + left - timedelta(days=idle_days)
    async with db.transaction() as session:
        session.add(
            AgentCredentialBinding(
                agent_id="agt_idle",
                credential_id=credential_id,
                created_by="usr_owner",
                bound_at=renewed,
                renewed_at=renewed,
                max_idle_period=idle_days * _DAY,
                idle_warning=warning,
                suspended=suspended,
                expires_at=expires_at,
            )
        )


async def _events(db: _MemoryDb) -> list[Event]:
    async with db.session() as session:
        return await EventRepository.list_all(
            session, event_type=[EventType.CREDENTIAL_GRANT_IDLE_EXPIRING]
        )


async def _warned_at(db: _MemoryDb, credential_id: str) -> datetime | None:
    async with db.session() as session:
        binding = (
            await session.execute(
                AgentCredentialBinding.__table__.select().where(
                    AgentCredentialBinding.credential_id == credential_id
                )
            )
        ).one()
        return cast(datetime | None, binding.idle_warned_at)


async def test_warns_once_inside_the_window(admin_db: _MemoryDb) -> None:
    await _bind(admin_db, "crd_soon", idle_days=14, left=timedelta(hours=47))
    scanner = BindingIdleWarningScanner(admin_db)

    assert await scanner.sweep(now=_NOW) == 1
    events = await _events(admin_db)
    assert len(events) == 1
    event = events[0]
    assert event.requires_action is True
    assert event.created_by == "agt_idle"
    assert event.data["agent_id"] == "agt_idle"
    assert event.data["credential_id"] == "crd_soon"
    assert event.data["max_idle_period"] == "P14D"
    assert datetime.fromisoformat(event.data["idle_expires_at"]) == _NOW + timedelta(hours=47)
    assert "Reporter" in event.summary
    assert await _warned_at(admin_db, "crd_soon") is not None

    # The marker keeps the next day's run quiet.
    assert await scanner.sweep(now=_NOW + timedelta(hours=24)) == 0
    assert len(await _events(admin_db)) == 1


@pytest.mark.parametrize(
    ("idle_days", "left", "kwargs"),
    [
        pytest.param(14, timedelta(hours=49), {}, id="outside-window"),
        pytest.param(3, timedelta(hours=24), {}, id="idle-limit-under-4-days"),
        pytest.param(14, timedelta(hours=24), {"warning": False}, id="warning-off"),
        pytest.param(14, timedelta(hours=24), {"suspended": True}, id="suspended"),
        pytest.param(
            14,
            timedelta(hours=24),
            {"expires_at": _NOW - timedelta(hours=1)},
            id="end-date-passed",
        ),
        pytest.param(14, -timedelta(hours=1), {}, id="already-idle"),
    ],
)
async def test_skips_bindings_not_due(
    admin_db: _MemoryDb, idle_days: int, left: timedelta, kwargs: dict[str, object]
) -> None:
    await _bind(admin_db, "crd_quiet", idle_days=idle_days, left=left, **kwargs)  # type: ignore[arg-type]

    assert await BindingIdleWarningScanner(admin_db).sweep(now=_NOW) == 0
    assert await _events(admin_db) == []
    assert await _warned_at(admin_db, "crd_quiet") is None


async def test_a_use_clears_the_marker_for_the_next_idle_period(admin_db: _MemoryDb) -> None:
    await _bind(admin_db, "crd_used", idle_days=7, left=timedelta(hours=30))
    scanner = BindingIdleWarningScanner(admin_db)
    assert await scanner.sweep(now=_NOW) == 1

    # The agent uses the binding: the idle clock restarts and the marker clears.
    async with admin_db.transaction() as session:
        assert await BindingUseRepository.record_use(
            session,
            agent_id="agt_idle",
            credential_id="crd_used",
            now=_NOW + timedelta(hours=1),
            min_interval=timedelta(hours=1),
        )
    assert await _warned_at(admin_db, "crd_used") is None

    # Seven days on, it is due again and warns once more.
    later = _NOW + timedelta(hours=1) + timedelta(days=7) - timedelta(hours=30)
    assert await scanner.sweep(now=later) == 1
    assert len(await _events(admin_db)) == 2


async def test_window_and_minimum_follow_config(admin_db: _MemoryDb) -> None:
    await _bind(admin_db, "crd_cfg", idle_days=2, left=timedelta(hours=60))
    config = GrantExpiryConfig.model_validate(
        {"idle_warning_window": "PT72H", "idle_warning_min_length": "P1D"}
    )

    assert await BindingIdleWarningScanner(admin_db, config=config).sweep(now=_NOW) == 1


async def test_a_renewal_clears_the_marker(admin_db: _MemoryDb) -> None:
    await _bind(admin_db, "crd_renewed", idle_days=7, left=timedelta(hours=30))
    scanner = BindingIdleWarningScanner(admin_db)
    assert await scanner.sweep(now=_NOW) == 1

    # A renewal that changes nothing else.
    async with admin_db.transaction() as session:
        await AgentCredentialBindingRepository.renew(
            session, agent_id="agt_idle", credential_id="crd_renewed", policy={}, now=_NOW
        )
    assert await _warned_at(admin_db, "crd_renewed") is None
    assert await scanner.sweep(now=_NOW + timedelta(days=6)) == 1


async def _bind_until(
    db: _MemoryDb, credential_id: str, *, granted_days: int, left: timedelta
) -> None:
    """A binding granted for ``granted_days`` with no idle limit, ending in ``left``."""
    granted = _NOW + left - timedelta(days=granted_days)
    async with db.transaction() as session:
        session.add(
            AgentCredentialBinding(
                agent_id="agt_idle",
                credential_id=credential_id,
                created_by="usr_owner",
                bound_at=granted,
                renewed_at=granted,
                expires_at=_NOW + left,
                idle_warning=True,
            )
        )


async def _end_date_events(db: _MemoryDb) -> list[Event]:
    async with db.session() as session:
        return await EventRepository.list_all(
            session, event_type=[EventType.CREDENTIAL_GRANT_EXPIRING]
        )


async def test_warns_once_before_the_end_date(admin_db: _MemoryDb) -> None:
    await _bind_until(admin_db, "crd_ending", granted_days=30, left=timedelta(hours=40))
    scanner = BindingIdleWarningScanner(admin_db)

    assert await scanner.sweep(now=_NOW) == 1
    (event,) = await _end_date_events(admin_db)
    assert event.requires_action is True
    assert event.created_by == "agt_idle"
    assert event.data["credential_id"] == "crd_ending"
    assert datetime.fromisoformat(event.data["expires_at"]) == _NOW + timedelta(hours=40)
    assert "ends on" in event.summary
    assert await _events(admin_db) == []

    # A use does not move the end date, so it does not warn again.
    async with admin_db.transaction() as session:
        await BindingUseRepository.record_use(
            session,
            agent_id="agt_idle",
            credential_id="crd_ending",
            now=_NOW + timedelta(hours=1),
            min_interval=timedelta(hours=1),
        )
    assert await scanner.sweep(now=_NOW + timedelta(hours=24)) == 0

    # A renewal sets a new end date, which warns once more when it nears.
    async with admin_db.transaction() as session:
        await AgentCredentialBindingRepository.renew(
            session,
            agent_id="agt_idle",
            credential_id="crd_ending",
            policy={"expires_at": _NOW + timedelta(days=30)},
            now=_NOW,
        )
    assert await scanner.sweep(now=_NOW + timedelta(days=29)) == 1
    assert len(await _end_date_events(admin_db)) == 2


@pytest.mark.parametrize(
    ("granted_days", "left"),
    [
        pytest.param(30, timedelta(hours=49), id="outside-window"),
        pytest.param(3, timedelta(hours=24), id="granted-under-4-days"),
        pytest.param(30, -timedelta(hours=1), id="already-ended"),
    ],
)
async def test_skips_end_dates_not_due(
    admin_db: _MemoryDb, granted_days: int, left: timedelta
) -> None:
    await _bind_until(admin_db, "crd_quiet", granted_days=granted_days, left=left)

    assert await BindingIdleWarningScanner(admin_db).sweep(now=_NOW) == 0
    assert await _end_date_events(admin_db) == []


async def test_warns_about_both_deadlines_separately(admin_db: _MemoryDb) -> None:
    await _bind(
        admin_db,
        "crd_both",
        idle_days=14,
        left=timedelta(hours=30),
        expires_at=_NOW + timedelta(hours=40),
    )

    assert await BindingIdleWarningScanner(admin_db).sweep(now=_NOW) == 2
    assert len(await _events(admin_db)) == 1
    assert len(await _end_date_events(admin_db)) == 1
