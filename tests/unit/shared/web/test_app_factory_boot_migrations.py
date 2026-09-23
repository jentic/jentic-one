"""Boot-time identity migrations run as one sequenced task (theme-8 L2).

Key retirement may reuse an unstamped SA crash remnant and bind ``sva_`` rows
onto it; the SA → agent migration copies an SA's bindings and stamps it. Run
concurrently, those binds could land after the copy + stamp. These tests pin
the in-process order — the migration never starts before key retirement has
finished — and that each step keeps its own failure isolation. The job
services are replaced by in-memory fakes (no DB is touched here; the
real-DB serialisation is covered by the integration suite).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from jentic_one.shared.web import app_factory

_AF = "jentic_one.shared.web.app_factory"


def _ctx(*, dbs: set[str], sweep_age_hours: int = -1) -> MagicMock:
    ctx = MagicMock()
    ctx.has_db.side_effect = lambda name: name in dbs
    ctx.config.services.service_account_sweep_min_stamp_age_hours = sweep_age_hours
    return ctx


class _FakeKeyRetirement:
    def __init__(self, events: list[str], gate: asyncio.Event, *, fail: bool = False) -> None:
        self._events = events
        self._gate = gate
        self._fail = fail

    def __call__(self, _ctx: object) -> _FakeKeyRetirement:
        return self

    async def run(self) -> list[object]:
        self._events.append("key_retirement_started")
        await self._gate.wait()
        self._events.append("key_retirement_finished")
        if self._fail:
            raise RuntimeError("key retirement blew up")
        return []


class _FakeMigration:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __call__(self, _ctx: object) -> _FakeMigration:
        return self

    async def run(self) -> list[object]:
        self._events.append("sa_migration_started")
        return []

    async def sweep(self) -> None:
        self._events.append("sa_sweep")


@pytest.mark.parametrize("fail_key_retirement", [False, True])
async def test_sa_migration_starts_only_after_key_retirement_finishes(
    fail_key_retirement: bool,
) -> None:
    """The migration waits for key retirement; a failing key retirement is
    logged and swallowed and the migration still runs."""
    events: list[str] = []
    gate = asyncio.Event()
    with (
        patch(
            f"{_AF}.KeyRetirementService",
            _FakeKeyRetirement(events, gate, fail=fail_key_retirement),
        ),
        patch(f"{_AF}.ServiceAccountMigrationService", _FakeMigration(events)),
    ):
        task = app_factory._start_boot_migrations(
            _ctx(dbs={"admin", "control"}, sweep_age_hours=0), {"control"}
        )
        assert task is not None
        for _ in range(5):
            await asyncio.sleep(0)
        assert events == ["key_retirement_started"]  # migration held back

        gate.set()
        await asyncio.wait_for(task, timeout=5.0)

    assert events == [
        "key_retirement_started",
        "key_retirement_finished",
        "sa_migration_started",
        "sa_sweep",
    ]


async def test_negative_sweep_age_still_skips_the_automatic_sweep() -> None:
    events: list[str] = []
    gate = asyncio.Event()
    gate.set()
    with (
        patch(f"{_AF}.KeyRetirementService", _FakeKeyRetirement(events, gate)),
        patch(f"{_AF}.ServiceAccountMigrationService", _FakeMigration(events)),
    ):
        task = app_factory._start_boot_migrations(
            _ctx(dbs={"admin", "control"}, sweep_age_hours=-1), {"control"}
        )
        assert task is not None
        await asyncio.wait_for(task, timeout=5.0)

    assert events == ["key_retirement_started", "key_retirement_finished", "sa_migration_started"]


def test_boot_migrations_gated_on_control_app_and_both_dbs() -> None:
    """Broker-only (control DB granted, control app not enabled) and a control
    process missing the admin DB start neither job."""
    with patch(f"{_AF}.asyncio.create_task") as create_task:
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_boot_migrations(broker_ctx, {"broker"}) is None
        no_admin_ctx = _ctx(dbs={"control"})
        assert app_factory._start_boot_migrations(no_admin_ctx, {"control"}) is None
        create_task.assert_not_called()
