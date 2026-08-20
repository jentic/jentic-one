"""Integration tests for the telemetry lifespan wiring (plan item 29).

Exercises ``_start_telemetry`` / ``_stop_telemetry`` against a real admin DB:

- the migration-created ``instance_identities`` table is present and usable;
- ``instance_initialized`` is emitted exactly once (on the process that wins the
  insert) while ``instance_booted`` is emitted on every startup;
- the flush loop drains its queue on shutdown;
- nothing is resolved, persisted, or emitted when telemetry is disabled (the
  single consent gate).

The telemetry endpoint points at a refused localhost port so the flush loop's
drain-on-shutdown POST is exercised without any real network egress — the client
swallows the connection error by design.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, func, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.instance_identity import InstanceIdentity
from jentic_one.shared.config import AppConfig, TelemetryConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.events import EventType, HostOs
from jentic_one.shared.telemetry.sink import get_active_sink
from jentic_one.shared.web.app_factory import _start_telemetry, _stop_telemetry

pytestmark = pytest.mark.integration

# A refused port keeps drain-on-shutdown hermetic: the POST fails fast and the
# client swallows it. A high flush interval means the periodic tick never fires
# during the test — only the explicit ``drain()`` in ``_stop_telemetry`` does.
_ENABLED_TELEMETRY = TelemetryConfig(
    enabled=True,
    instance_id="lifecycle-test-instance",
    endpoint="http://127.0.0.1:1",
    flush_interval_s=3600.0,
)

_LIFECYCLE_TYPES = (EventType.INSTANCE_INITIALIZED, EventType.INSTANCE_BOOTED)


@pytest.fixture()
async def clean_telemetry_state(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Drop the identity row + any lifecycle events before and after each test."""

    async def _clean() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(InstanceIdentity))
            await session.execute(delete(Event).where(Event.type.in_(_LIFECYCLE_TYPES)))
            await session.commit()

    await _clean()
    yield
    await _clean()
    # Never leave a process-global sink dangling for the next test.
    await _stop_telemetry(None)


async def _count_events(admin_db: DatabaseSession, event_type: str) -> int:
    async with admin_db.session() as session:
        result = await session.execute(
            select(func.count()).select_from(Event).where(Event.type == event_type)
        )
        return int(result.scalar_one())


def _enabled_config(base: AppConfig) -> AppConfig:
    return base.model_copy(update={"telemetry": _ENABLED_TELEMETRY})


async def test_first_startup_emits_initialized_and_booted_once_each(
    integration_config: AppConfig, admin_db: DatabaseSession, clean_telemetry_state: None
) -> None:
    """A fresh instance emits instance_initialized once and instance_booted once."""
    async with Context(_enabled_config(integration_config)) as ctx:
        handle = await _start_telemetry(ctx)
        try:
            assert handle is not None
            assert ctx.instance_id == "lifecycle-test-instance"
            assert get_active_sink() is not None
        finally:
            await _stop_telemetry(handle)

    assert await _count_events(admin_db, EventType.INSTANCE_INITIALIZED) == 1
    assert await _count_events(admin_db, EventType.INSTANCE_BOOTED) == 1

    # The one-time event carries the OS family tag (and only that) — the OS is
    # never attached to any other event.
    async with admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == EventType.INSTANCE_INITIALIZED)
        )
        initialized = result.scalar_one()
        assert initialized.data["tags"] == [str(HostOs.current())]
        booted = await session.execute(select(Event).where(Event.type == EventType.INSTANCE_BOOTED))
        assert not (booted.scalar_one().data or {}).get("tags")


async def test_second_startup_reboots_without_reinitializing(
    integration_config: AppConfig, admin_db: DatabaseSession, clean_telemetry_state: None
) -> None:
    """instance_initialized stays at one across restarts; instance_booted increments."""
    config = _enabled_config(integration_config)

    async with Context(config) as ctx:
        first = await _start_telemetry(ctx)
        await _stop_telemetry(first)

    async with Context(config) as ctx:
        second = await _start_telemetry(ctx)
        await _stop_telemetry(second)

    # The identity row is the dedupe: only the first boot performs the insert.
    assert await _count_events(admin_db, EventType.INSTANCE_INITIALIZED) == 1
    assert await _count_events(admin_db, EventType.INSTANCE_BOOTED) == 2


async def test_shutdown_drains_the_flush_queue(
    integration_config: AppConfig, admin_db: DatabaseSession, clean_telemetry_state: None
) -> None:
    """The lifecycle emits queue events; _stop_telemetry drains them on shutdown."""
    async with Context(_enabled_config(integration_config)) as ctx:
        handle = await _start_telemetry(ctx)
        assert handle is not None
        sink = get_active_sink()
        assert sink is not None
        # The two lifecycle events were forwarded to the sink and, with a 1h flush
        # interval, are still queued (the periodic tick hasn't fired).
        assert sink.queue.qsize() >= 1

        await _stop_telemetry(handle)

        # drain() flushed the queue one last time (to the refused endpoint,
        # swallowed) — nothing is left buffered.
        assert sink.queue.qsize() == 0
        assert get_active_sink() is None


async def test_disabled_telemetry_resolves_and_emits_nothing(
    integration_config: AppConfig, admin_db: DatabaseSession, clean_telemetry_state: None
) -> None:
    """With telemetry off, no id is resolved/persisted and no events are emitted."""
    # integration_config ships with telemetry disabled (the default).
    async with Context(integration_config) as ctx:
        handle = await _start_telemetry(ctx)
        assert handle is None
        assert ctx.instance_id is None
        assert get_active_sink() is None

    async with admin_db.session() as session:
        rows = await session.execute(select(func.count()).select_from(InstanceIdentity))
        assert int(rows.scalar_one()) == 0
    assert await _count_events(admin_db, EventType.INSTANCE_INITIALIZED) == 0
    assert await _count_events(admin_db, EventType.INSTANCE_BOOTED) == 0
