"""Post-migration data steps, run by the migration runner.

``python -m jentic_one.migrations.run`` calls :meth:`UpgradeStepService.run`
once every database is at head. A step spans the control and admin databases,
so it cannot live inside a single Alembic tree; running it from the migration
runner means every install path that already migrates (the Helm pre-upgrade
hook, ``jenticctl``, ``make migrate``, a hand-run upgrade) performs it before
the new version serves traffic.

Registered steps:

- ``rule_sets_mark_curated`` (repeatable) marks the shared permission rule
  sets created by a system actor or an ``org:admin`` as curated, and warns
  about each binding attached to a non-curated set its agent's owner did not
  create
  (:class:`~jentic_one.control.services.rule_set_curation.RuleSetCurationService`).

The theme-5 steps (``theme5_retire_toolkit_keys``,
``theme5_flatten_toolkits``) no longer exist; rows they recorded remain in the
ledger as history.

A step is a coroutine taking the :class:`Context` and returning an
:class:`UpgradeStepOutcome`. The service:

- skips a step the operator named in ``--skip-upgrade-step`` (not ledgered —
  it runs on the next full upgrade);
- reports ``already_done`` for a one-shot step the ledger already records
  (a one-shot step runs **at most once** per install);
- runs a **repeatable** step on every full upgrade, whether or not the ledger
  records it. A repeatable step must be idempotent; it exists for data a
  release older than the step's can still write against the newer schema
  (an older release redeployed on it), which a single run would miss;
- runs the step; a ``performed`` or ``skipped`` outcome is ledgered (a
  repeatable step's row is overwritten with its latest run), a ``failed`` one
  (or a raised exception) is not, so the next run retries it.

:meth:`UpgradeStepService.pending` lists the registered steps the ledger does
not record — what ``migrations.run --check`` reports as pending.

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
from jentic_one.control.services.rule_set_curation import (
    CrossOwnerAttachment,
    RuleSetCurationService,
)
from jentic_one.control.services.run_lock import UPGRADE_STEPS_LOCK_KEY, hold_run_lock
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError

logger = structlog.get_logger(__name__)

#: ``created_by`` for ledger rows — attributable to the runner, not a user.
_LEDGER_ACTOR = "system:upgrade-steps"

#: Outcome actions that complete a step (ledgered; a one-shot step never runs again).
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
    #: Run on every full upgrade instead of once (see the module docstring).
    repeatable: bool = False


RULE_SETS_MARK_CURATED = "rule_sets_mark_curated"


#: Leads the per-binding warnings of ``rule_sets_mark_curated``.
CROSS_OWNER_RULE_SET_WARNING = (
    "{count} agent credential binding(s) use a non-curated shared rule set created by "
    "someone other than the agent's owner; that creator can still edit the rules that "
    "govern the agent. Left attached. To resolve one: the agent's owner attaches a set "
    "they created or a curated set, or detaches to the binding's inline rules; or an "
    "org:admin re-attaches a curated set, or marks the set curated (control DB: UPDATE "
    "permission_rule_sets SET curated = true WHERE id = '<rule set id>'), after which "
    "only an org:admin can edit it. The bindings:"
)


def _cross_owner_line(a: CrossOwnerAttachment) -> str:
    return (
        f"binding {a.binding_id}: agent {a.agent_id} ({a.agent_name!r}) owned by "
        f"{a.owner_id or 'no owner'}, credential {a.credential_id}, rule set "
        f"{a.rule_set_id} ({a.rule_set_name!r}) created by {a.rule_set_creator}"
    )


async def _mark_curated_rule_sets(ctx: Context) -> UpgradeStepOutcome:
    result = await RuleSetCurationService(ctx).mark_existing()
    warnings: tuple[str, ...] = ()
    if result.cross_owner:
        warnings = (
            CROSS_OWNER_RULE_SET_WARNING.format(count=len(result.cross_owner)),
            *(_cross_owner_line(a) for a in result.cross_owner),
        )
    return UpgradeStepOutcome(
        name=RULE_SETS_MARK_CURATED,
        action="performed",
        summary={
            "marked": result.marked,
            "admin_creators": result.admin_creators,
            "cross_owner_bindings": len(result.cross_owner),
        },
        warnings=warnings,
    )


#: Every step, in run order.
STEPS: tuple[UpgradeStepSpec, ...] = (
    UpgradeStepSpec(name=RULE_SETS_MARK_CURATED, run=_mark_curated_rule_sets, repeatable=True),
)

#: Names of deleted steps that ``--skip-upgrade-step`` still accepts (as a
#: no-op) so a deployment that pinned one in its migrate arguments on 0.40
#: (e.g. Helm ``migrate.extraArgs``) does not fail argument parsing here.
RETIRED_STEP_NAMES: tuple[str, ...] = ("theme5_retire_toolkit_keys", "theme5_flatten_toolkits")


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

    async def pending(self) -> list[str]:
        """Registered steps the ledger does not record, in run order.

        A repeatable step counts as done once recorded: the next full upgrade
        reruns it regardless, so its row alone says it has run on this schema.
        """
        if not self._steps:
            return []
        async with self._ctx.control_db.session() as session:
            done = await UpgradeStepRepository.list_names(session)
        return [step.name for step in self._steps if step.name not in done]

    async def _run_one(self, step: UpgradeStepSpec) -> UpgradeStepOutcome:
        async with self._ctx.control_db.session() as session:
            done = await UpgradeStepRepository.get(session, step.name)
        if done is not None and not step.repeatable:
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
            await self._record(step, outcome.summary)
        logger.info("upgrade_step", step=step.name, action=outcome.action)
        return outcome

    async def _record(self, step: UpgradeStepSpec, summary: dict[str, Any]) -> None:
        repo = UpgradeStepRepository
        write = repo.record_run if step.repeatable else repo.record
        try:
            async with self._ctx.control_db.transaction() as session:
                await write(
                    session,
                    name=step.name,
                    tool_version=__version__,
                    summary=summary,
                    created_by=_LEDGER_ACTOR,
                )
        except DatabaseIntegrityError:
            # A concurrent runner recorded the step first (SQLite has no run
            # lock); a registered step must be idempotent, so its record stands.
            logger.info("upgrade_step_already_recorded", step=step.name)


def _skipped(name: str, reason: str) -> UpgradeStepOutcome:
    return UpgradeStepOutcome(name=name, action="skipped", summary={"reason": reason})
