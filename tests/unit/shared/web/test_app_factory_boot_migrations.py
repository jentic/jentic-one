"""Boot-time identity migration task (theme-8 L2).

The control plane runs the SA → agent migration (and its automatic sweep) as
one boot task. Theme-5 Phase 6b deleted the key-retirement step that used to
run first, so the migration now starts straight away. The job service is
replaced by an in-memory fake (no DB is touched here; the real-DB behaviour is
covered by the integration suite).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from jentic_one.shared.web import app_factory

_AF = "jentic_one.shared.web.app_factory"


def _ctx(*, dbs: set[str], sweep_age_hours: int = -1) -> MagicMock:
    ctx = MagicMock()
    ctx.has_db.side_effect = lambda name: name in dbs
    ctx.config.services.service_account_sweep_min_stamp_age_hours = sweep_age_hours
    return ctx


class _FakeMigration:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    def __call__(self, _ctx: object) -> _FakeMigration:
        return self

    async def run(self) -> list[object]:
        self._events.append("sa_migration_started")
        if self._fail:
            raise RuntimeError("sa migration blew up")
        return []

    async def sweep(self) -> None:
        self._events.append("sa_sweep")


async def test_sa_migration_runs_then_sweeps() -> None:
    events: list[str] = []
    with patch(f"{_AF}.ServiceAccountMigrationService", _FakeMigration(events)):
        task = app_factory._start_boot_migrations(
            _ctx(dbs={"admin", "control"}, sweep_age_hours=0), {"control"}
        )
        assert task is not None
        await asyncio.wait_for(task, timeout=5.0)

    assert events == ["sa_migration_started", "sa_sweep"]


async def test_negative_sweep_age_still_skips_the_automatic_sweep() -> None:
    events: list[str] = []
    with patch(f"{_AF}.ServiceAccountMigrationService", _FakeMigration(events)):
        task = app_factory._start_boot_migrations(
            _ctx(dbs={"admin", "control"}, sweep_age_hours=-1), {"control"}
        )
        assert task is not None
        await asyncio.wait_for(task, timeout=5.0)

    assert events == ["sa_migration_started"]


async def test_failing_sa_migration_is_swallowed() -> None:
    """A failing migration is logged, never crashes the boot task."""
    events: list[str] = []
    with patch(f"{_AF}.ServiceAccountMigrationService", _FakeMigration(events, fail=True)):
        task = app_factory._start_boot_migrations(
            _ctx(dbs={"admin", "control"}, sweep_age_hours=0), {"control"}
        )
        assert task is not None
        await asyncio.wait_for(task, timeout=5.0)

    assert events == ["sa_migration_started"]


def test_boot_migrations_gated_on_control_app_and_both_dbs() -> None:
    """Broker-only (control DB granted, control app not enabled) and a control
    process missing the admin DB start no job."""
    with patch(f"{_AF}.asyncio.create_task") as create_task:
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_boot_migrations(broker_ctx, {"broker"}) is None
        no_admin_ctx = _ctx(dbs={"control"})
        assert app_factory._start_boot_migrations(no_admin_ctx, {"control"}) is None
        create_task.assert_not_called()
