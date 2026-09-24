"""One-shot post-migration data steps, run by the migration runner.

``python -m jentic_one.migrations.run`` calls :meth:`UpgradeStepService.run`
once every database is at head. A step spans the control and admin databases,
so it cannot live inside a single Alembic tree; running it from the migration
runner means every install path that already migrates (the Helm pre-upgrade
hook, ``jenticctl``, ``make migrate``, a hand-run upgrade) performs it before
the new version serves traffic.

**No steps are registered in this release.** The theme-5 steps
(``theme5_retire_toolkit_keys``, ``theme5_flatten_toolkits``) were deleted
with the toolkit tables they read (Phase 6b); the ledger (``upgrade_steps``)
and this runner stay for the next release's data steps. Rows the theme-5 steps
recorded remain in the ledger as history.

A step is a coroutine taking the :class:`Context` and returning an
:class:`UpgradeStepOutcome`. The service:

- skips a step the operator named in ``--skip-upgrade-step`` (not ledgered —
  it runs on the next full upgrade);
- reports ``already_done`` for a step the ledger already records (each
  registered step runs **at most once** per install);
- runs the step; a ``performed`` or ``skipped`` outcome is ledgered, a
  ``failed`` one (or a raised exception) is not, so the next run retries it.

The whole run holds the upgrade-steps run lock so concurrent runners never
interleave.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.upgrade_step_repo import UpgradeStepRepository
from jentic_one.control.services.run_lock import UPGRADE_STEPS_LOCK_KEY, hold_run_lock
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError

logger = structlog.get_logger(__name__)

#: ``created_by`` for ledger rows — attributable to the runner, not a user.
_LEDGER_ACTOR = "system:upgrade-steps"

#: Outcome actions that complete a step (ledgered, never run again).
_COMPLETING_ACTIONS = frozenset({"performed", "skipped"})


@dataclass(frozen=True)
class UpgradeStepOutcome:
    """What one step did on this run."""

    name: str
    #: ``performed`` | ``already_done`` | ``skipped`` | ``failed``
    action: str
    summary: dict[str, Any] = field(default_factory=dict)
    #: True when the step left work undone that must be fixed before the new
    #: version serves traffic (the runner exits non-zero).
    failed: bool = False
    #: Non-blocking follow-ups for the operator, each naming its recovery step.
    warnings: tuple[str, ...] = ()


StepFn = Callable[[Context], Awaitable[UpgradeStepOutcome]]


@dataclass(frozen=True)
class UpgradeStepSpec:
    """A registered step: its stable ledger name and its body."""

    name: str
    run: StepFn


#: Every step, in run order. Empty since theme-5 Phase 6b (see module docstring).
STEPS: tuple[UpgradeStepSpec, ...] = ()


def step_names() -> tuple[str, ...]:
    """Registered step names, in run order (the runner's ``--skip-upgrade-step`` choices)."""
    return tuple(step.name for step in STEPS)


class UpgradeStepService:
    """Runs the registered post-migration steps under the upgrade-steps run lock."""

    def __init__(self, ctx: Context, *, steps: Sequence[UpgradeStepSpec] | None = None) -> None:
        self._ctx = ctx
        self._steps = tuple(STEPS if steps is None else steps)

    async def run(self, *, skip: Collection[str] = ()) -> list[UpgradeStepOutcome]:
        """Run every step not named in ``skip`` (a skipped step is not ledgered)."""
        if not self._steps:
            return []
        async with hold_run_lock(self._ctx, UPGRADE_STEPS_LOCK_KEY):
            outcomes: list[UpgradeStepOutcome] = []
            for step in self._steps:
                if step.name in skip:
                    outcomes.append(_skipped(step.name, "operator_skipped"))
                    continue
                outcomes.append(await self._run_one(step))
            return outcomes

    async def _run_one(self, step: UpgradeStepSpec) -> UpgradeStepOutcome:
        async with self._ctx.control_db.session() as session:
            done = await UpgradeStepRepository.get(session, step.name)
        if done is not None:
            return UpgradeStepOutcome(
                name=step.name, action="already_done", summary=done.summary or {}
            )
        try:
            outcome = await step.run(self._ctx)
        except Exception as exc:
            # Not ledgered: the next run retries the step after the fix.
            logger.exception("upgrade_step_failed", step=step.name)
            return UpgradeStepOutcome(
                name=step.name,
                action="failed",
                summary={"error": f"{type(exc).__name__}: {exc}"},
                failed=True,
            )
        if outcome.action in _COMPLETING_ACTIONS and not outcome.failed:
            await self._record(step.name, outcome.summary)
        logger.info("upgrade_step", step=step.name, action=outcome.action)
        return outcome

    async def _record(self, name: str, summary: dict[str, Any]) -> None:
        try:
            async with self._ctx.control_db.transaction() as session:
                await UpgradeStepRepository.record(
                    session,
                    name=name,
                    tool_version=__version__,
                    summary=summary,
                    created_by=_LEDGER_ACTOR,
                )
        except DatabaseIntegrityError:
            # A concurrent runner recorded the step first (SQLite has no run
            # lock); a registered step must be idempotent, so its record stands.
            logger.info("upgrade_step_already_recorded", step=name)


def _skipped(name: str, reason: str) -> UpgradeStepOutcome:
    return UpgradeStepOutcome(name=name, action="skipped", summary={"reason": reason})
