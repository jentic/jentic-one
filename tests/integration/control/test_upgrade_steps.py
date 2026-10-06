"""Integration tests for ``UpgradeStepService`` — the post-migration step ledger.

Runs against real control databases on both dialects (Postgres takes the
advisory-lock path; SQLite the serialised-writer path). These tests drive the
mechanism with an injected step: the ledger, repeatable steps, pending
steps, retry-after-failure, and concurrent runs converging. The registered
steps have their own tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, text

from jentic_one.control.core.schema.upgrade_steps import UpgradeStep
from jentic_one.control.repos.upgrade_step_repo import UpgradeStepRepository
from jentic_one.control.services.upgrade_steps import (
    RULE_SETS_MARK_CURATED,
    STEPS,
    UpgradeStepOutcome,
    UpgradeStepService,
    UpgradeStepSpec,
    step_names,
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


def _counting_step(
    calls: list[int], *, fail_first: bool = False, repeatable: bool = False
) -> UpgradeStepSpec:
    async def _run(_ctx: Context) -> UpgradeStepOutcome:
        calls.append(1)
        if fail_first and len(calls) == 1:
            raise RuntimeError("simulated step failure")
        return UpgradeStepOutcome(name=_STEP, action="performed", summary={"n": len(calls)})

    return UpgradeStepSpec(name=_STEP, run=_run, repeatable=repeatable)


def test_registered_steps() -> None:
    assert step_names() == (RULE_SETS_MARK_CURATED,)
    assert [step.repeatable for step in STEPS] == [True]


async def test_empty_registry_is_a_no_op(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    assert await UpgradeStepService(integration_context, steps=[]).run() == []
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


async def test_repeatable_step_runs_every_time_and_keeps_one_row(
    integration_context: Context, control_db: DatabaseSession, clean_ledger: None
) -> None:
    calls: list[int] = []
    svc = UpgradeStepService(integration_context, steps=[_counting_step(calls, repeatable=True)])

    first = await svc.run()
    second = await svc.run()

    assert [o.action for o in first + second] == ["performed", "performed"]
    assert len(calls) == 2
    assert await _ledger(control_db) == [_STEP]
    async with control_db.session() as session:
        row = await UpgradeStepRepository.get(session, _STEP)
    assert row is not None and row.summary == {"n": 2}, "the row records the latest run"


async def test_pending_lists_steps_the_ledger_does_not_record(
    integration_context: Context, clean_ledger: None
) -> None:
    calls: list[int] = []
    svc = UpgradeStepService(integration_context, steps=[_counting_step(calls, repeatable=True)])

    assert await svc.pending() == [_STEP]
    await svc.run(skip={_STEP})
    assert await svc.pending() == [_STEP], "an operator skip leaves the step pending"
    await svc.run()
    assert await svc.pending() == []
    assert await UpgradeStepService(integration_context, steps=[]).pending() == []


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
