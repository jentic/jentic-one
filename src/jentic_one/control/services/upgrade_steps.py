"""One-shot post-migration data steps, run by the migration runner.

``python -m jentic_one.migrations.run`` calls :meth:`UpgradeStepService.run`
once every database is at head. The steps here span the control and admin
databases, so they cannot live inside a single Alembic tree; running them from
the migration runner means every install path that already migrates (the Helm
pre-upgrade hook, ``jenticctl``, ``make migrate``, a hand-run upgrade) performs
them before the new version serves traffic.

The theme-5 steps, in order:

1. **Toolkit-key retirement** — every resolvable ``jntc_live_`` key gains its
   successor service account (:class:`KeyRetirementService`). Idempotent per
   key and also run at boot, so it is not ledgered: it simply runs each time.
2. **Toolkit flattening** — every ``(agent, credential)`` pair reachable
   through a toolkit gains a direct binding (:class:`ToolkitFlatteningService`).
   Without it, the default direct-binding broker path authorizes no existing
   toolkit-bound agent. Ledgered and run **at most once**: a later re-run would
   re-derive bindings from the retained toolkit rows and restore access an
   operator has since purged. Skipped (and ledgered) when an operator already
   recorded a verified flatten (``flatten-toolkits --verify --acknowledge``).

The whole run holds the upgrade-steps run lock so concurrent runners never
interleave. The Phase-6b acknowledgement is never written here — the table
drops stay gated on an explicit operator verification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.upgrade_step_repo import (
    UPGRADE_STEPS_LOCK_KEY,
    UpgradeStepRepository,
)
from jentic_one.control.services.key_retirement import KeyRetirementService
from jentic_one.control.services.run_lock import hold_run_lock
from jentic_one.control.services.toolkit_flattening import ToolkitFlatteningService
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError

logger = structlog.get_logger(__name__)

STEP_RETIRE_TOOLKIT_KEYS = "theme5_retire_toolkit_keys"
STEP_FLATTEN_TOOLKITS = "theme5_flatten_toolkits"

#: ``created_by`` for ledger rows — attributable to the runner, not a user.
_LEDGER_ACTOR = "system:upgrade-steps"

#: Report categories that are per-binding bookkeeping rather than something an
#: operator needs to review; summarised as counts only.
_BOOKKEEPING_CATEGORIES = frozenset({"binding_created", "binding_would_create"})


@dataclass(frozen=True)
class UpgradeStepOutcome:
    """What one step did on this run."""

    name: str
    #: ``performed`` | ``already_done`` | ``skipped``
    action: str
    summary: dict[str, Any] = field(default_factory=dict)
    #: True when the step ran but left work undone that the operator must fix
    #: (the runner exits non-zero so the upgrade stops before serving traffic).
    failed: bool = False


class UpgradeStepService:
    """Runs the post-migration steps under the upgrade-steps run lock."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def run(self) -> list[UpgradeStepOutcome]:
        async with hold_run_lock(self._ctx, UPGRADE_STEPS_LOCK_KEY):
            return [await self._retire_toolkit_keys(), await self._flatten_toolkits()]

    async def _retire_toolkit_keys(self) -> UpgradeStepOutcome:
        outcomes = await KeyRetirementService(self._ctx).run()
        by_action = Counter(o.action for o in outcomes)
        skipped = Counter(o.reason for o in outcomes if o.action == "skipped")
        summary: dict[str, Any] = {
            "migrated": by_action["migrated"],
            "already_migrated": by_action["already_migrated"],
            "skipped": dict(sorted(skipped.items())),
            "failed": by_action["failed"],
        }
        logger.info("upgrade_step", step=STEP_RETIRE_TOOLKIT_KEYS, **summary)
        return UpgradeStepOutcome(
            name=STEP_RETIRE_TOOLKIT_KEYS,
            action="performed",
            summary=summary,
            failed=by_action["failed"] > 0,
        )

    async def _flatten_toolkits(self) -> UpgradeStepOutcome:
        async with self._ctx.control_db.session() as session:
            done = await UpgradeStepRepository.get(session, STEP_FLATTEN_TOOLKITS)
            acks = await UpgradeStepRepository.count_flattening_acknowledgements(session)
        if done is not None:
            return UpgradeStepOutcome(
                name=STEP_FLATTEN_TOOLKITS, action="already_done", summary=done.summary or {}
            )
        if acks:
            summary: dict[str, Any] = {"reason": "verified_flatten_acknowledged", "acks": acks}
            await self._record(STEP_FLATTEN_TOOLKITS, summary)
            return UpgradeStepOutcome(name=STEP_FLATTEN_TOOLKITS, action="skipped", summary=summary)

        result = await ToolkitFlatteningService(self._ctx).run()
        review = Counter(
            f.category for f in result.findings if f.category not in _BOOKKEEPING_CATEGORIES
        )
        summary = {
            "pairs_total": result.pairs_total,
            "created": result.created,
            "created_default_deny": sum(
                1
                for f in result.findings
                if f.category == "binding_created" and f.detail.get("default_deny")
            ),
            "already_present": result.already_present,
            "findings": dict(sorted(review.items())),
        }
        await self._record(STEP_FLATTEN_TOOLKITS, summary)
        logger.info("upgrade_step", step=STEP_FLATTEN_TOOLKITS, **summary)
        return UpgradeStepOutcome(name=STEP_FLATTEN_TOOLKITS, action="performed", summary=summary)

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
            # lock); the step is idempotent, so its record stands.
            logger.info("upgrade_step_already_recorded", step=name)
