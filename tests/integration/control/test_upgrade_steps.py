"""Integration tests for ``UpgradeStepService`` — the post-migration step ledger.

Runs against real control databases on both dialects (Postgres takes the
advisory-lock path; SQLite the serialised-writer path). No step is registered
since theme-5 Phase 6b deleted the toolkit steps, so these tests drive the
mechanism with an injected step: the ledger, retry-after-failure, and
concurrent runs converging.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, text

from jentic_one.control.core.schema.upgrade_steps import UpgradeStep
from jentic_one.control.services.upgrade_steps import (
    STEPS,
    UpgradeStepOutcome,
    UpgradeStepService,
    UpgradeStepSpec,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_STEP = "test_step_ledger"


@pytest.fixture()
async def clean_ledger(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _cleanup() -> None:
        async with control_db.session() as session:
            await session.execute(delete(UpgradeStep))
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _ledger(control_db: DatabaseSession) -> list[str]:
    async with control_db.session() as session:
        rows = (await session.execute(text("SELECT name FROM upgrade_steps"))).all()
    return [str(r.name) for r in rows]


def _counting_step(calls: list[int], *, fail_first: bool = False) -> UpgradeStepSpec:
    async def _run(_ctx: Context) -> UpgradeStepOutcome:
        calls.append(1)
        if fail_first and len(calls) == 1:
            raise RuntimeError("simulated step failure")
        return UpgradeStepOutcome(name=_STEP, action="performed", summary={"n": len(calls)})

    return UpgradeStepSpec(name=_STEP, run=_run)


def test_no_steps_registered_after_phase_6b() -> None:
    """The theme-5 steps died with the toolkit tables; the next release adds its own."""
    assert STEPS == ()


async def test_empty_registry_is_a_no_op(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    assert await UpgradeStepService(integration_context).run() == []
    assert await _ledger(control_db) == []


async def test_step_runs_once_and_is_ledgered(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    calls: list[int] = []
    svc = UpgradeStepService(integration_context, steps=[_counting_step(calls)])

    first = await svc.run()
    second = await svc.run()

    assert [o.action for o in first] == ["performed"]
    assert [o.action for o in second] == ["already_done"]
    assert len(calls) == 1
    assert await _ledger(control_db) == [_STEP]


async def test_failed_step_is_not_ledgered_and_retries(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    calls: list[int] = []
    svc = UpgradeStepService(integration_context, steps=[_counting_step(calls, fail_first=True)])

    failed = await svc.run()
    assert failed[0].action == "failed" and failed[0].failed
    assert await _ledger(control_db) == []

    retried = await svc.run()
    assert retried[0].action == "performed"
    assert await _ledger(control_db) == [_STEP]


async def test_operator_skip_is_not_ledgered(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    calls: list[int] = []
    svc = UpgradeStepService(integration_context, steps=[_counting_step(calls)])

    outcomes = await svc.run(skip={_STEP})

    assert outcomes[0].action == "skipped"
    assert calls == []
    assert await _ledger(control_db) == []


async def test_concurrent_runs_converge(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    """Two runners (e.g. a retried hook overlapping its predecessor) converge."""
    calls: list[int] = []
    step = _counting_step(calls)
    runs = await asyncio.gather(
        UpgradeStepService(integration_context, steps=[step]).run(),
        UpgradeStepService(integration_context, steps=[step]).run(),
    )

    actions = sorted(run[0].action for run in runs)
    if control_db.engine.dialect.name == "postgresql":
        # The run lock serialises the runners: the second sees the ledger row.
        assert actions == ["already_done", "performed"]
    else:
        # No advisory locks on SQLite; the ledger's unique name makes an
        # overlapping run harmless (a registered step must be idempotent).
        assert set(actions) <= {"already_done", "performed"}
    assert await _ledger(control_db) == [_STEP]
