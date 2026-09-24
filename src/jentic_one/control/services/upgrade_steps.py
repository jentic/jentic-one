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
   key and also run at every control-plane boot, so it is not ledgered and
   never blocks the upgrade: a key that fails (or has no resolvable owner) is
   reported as a warning with its recovery command, and the boot run and the
   ``retire-toolkit-keys`` CLI pick it up later.
2. **Toolkit flattening** — every ``(agent, credential)`` pair reachable
   through a toolkit gains a direct binding (:class:`ToolkitFlatteningService`).
   Without it, the default direct-binding broker path authorizes no existing
   toolkit-bound agent, so a failure here **is** fatal to the upgrade (the
   runner exits non-zero). Ledgered and run **at most once**: a later re-run
   would re-derive bindings from the retained toolkit rows and restore access
   an operator has since purged. Skipped (and ledgered) when an operator
   already recorded a verified flatten (``flatten-toolkits --verify
   --acknowledge``).

Both steps read the legacy toolkit tables; once the Phase-6b drop migrations
have removed them there is nothing to do and each step reports ``skipped``
(reason ``toolkit_tables_absent``).

The whole run holds the upgrade-steps run lock so concurrent runners never
interleave. The Phase-6b acknowledgement is never written here — the table
drops stay gated on an explicit operator verification.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.upgrade_step_repo import UpgradeStepRepository
from jentic_one.control.services.key_retirement import KeyRetirementService
from jentic_one.control.services.run_lock import UPGRADE_STEPS_LOCK_KEY, hold_run_lock
from jentic_one.control.services.toolkit_flattening import ToolkitFlatteningService
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError

logger = structlog.get_logger(__name__)

STEP_RETIRE_TOOLKIT_KEYS = "theme5_retire_toolkit_keys"
STEP_FLATTEN_TOOLKITS = "theme5_flatten_toolkits"
#: Every step, in run order (the runner's ``--skip-upgrade-step`` choices).
STEP_NAMES: tuple[str, ...] = (STEP_RETIRE_TOOLKIT_KEYS, STEP_FLATTEN_TOOLKITS)

#: ``created_by`` for ledger rows — attributable to the runner, not a user.
_LEDGER_ACTOR = "system:upgrade-steps"

#: Report categories that are per-binding bookkeeping rather than something an
#: operator needs to review; summarised as counts only.
_BOOKKEEPING_CATEGORIES = frozenset({"binding_created", "binding_would_create"})

#: Legacy tables the steps read, per database. Absent once Phase 6b drops them.
_CONTROL_LEGACY_TABLE = "toolkits"
_ADMIN_LEGACY_TABLE = "agent_toolkit_bindings"


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


class UpgradeStepService:
    """Runs the post-migration steps under the upgrade-steps run lock."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def run(self, *, skip: Collection[str] = ()) -> list[UpgradeStepOutcome]:
        """Run every step not named in ``skip`` (a skipped step is not ledgered)."""
        async with hold_run_lock(self._ctx, UPGRADE_STEPS_LOCK_KEY):
            tables_present = await self._legacy_tables_present()
            outcomes: list[UpgradeStepOutcome] = []
            for name, step in (
                (STEP_RETIRE_TOOLKIT_KEYS, self._retire_toolkit_keys),
                (STEP_FLATTEN_TOOLKITS, self._flatten_toolkits),
            ):
                if name in skip:
                    outcomes.append(_skipped(name, "operator_skipped"))
                elif not tables_present:
                    outcomes.append(_skipped(name, "toolkit_tables_absent"))
                else:
                    outcomes.append(await step())
            return outcomes

    async def _legacy_tables_present(self) -> bool:
        async with self._ctx.control_db.session() as session:
            control = await UpgradeStepRepository.has_table(session, _CONTROL_LEGACY_TABLE)
        async with self._ctx.admin_db.session() as session:
            admin = await UpgradeStepRepository.has_table(session, _ADMIN_LEGACY_TABLE)
        return control and admin

    async def _retire_toolkit_keys(self) -> UpgradeStepOutcome:
        try:
            outcomes = await KeyRetirementService(self._ctx).run()
        except Exception as exc:
            # Never fatal: the control-plane boot re-runs the job, and the CLI
            # is the manual path. Blocking the upgrade here would also block the
            # flatten, which is the step agent access actually depends on.
            logger.exception("upgrade_step_failed", step=STEP_RETIRE_TOOLKIT_KEYS)
            return UpgradeStepOutcome(
                name=STEP_RETIRE_TOOLKIT_KEYS,
                action="failed",
                summary={"error": f"{type(exc).__name__}: {exc}"},
                warnings=(
                    "toolkit-key retirement did not run; the control plane retries it at "
                    "boot, or run `jentic_one retire-toolkit-keys` after the upgrade",
                ),
            )
        by_action = Counter(o.action for o in outcomes)
        skipped = Counter(o.reason for o in outcomes if o.action == "skipped")
        summary: dict[str, Any] = {
            "migrated": by_action["migrated"],
            "already_migrated": by_action["already_migrated"],
            "skipped": dict(sorted(skipped.items())),
            "failed": by_action["failed"],
        }
        warnings: list[str] = []
        if by_action["failed"]:
            warnings.append(
                f"{by_action['failed']} toolkit key(s) failed to migrate (see the "
                "toolkit_key_retirement_key_failed log lines); their holders cannot "
                "authenticate until you fix the cause and run `jentic_one retire-toolkit-keys`"
            )
        if skipped["owner_unresolved"]:
            warnings.append(
                f"{skipped['owner_unresolved']} toolkit key(s) have no resolvable owner and "
                "were NOT migrated; their holders cannot authenticate until you run "
                "`jentic_one retire-toolkit-keys --owner <admin-email>`"
            )
        logger.info("upgrade_step", step=STEP_RETIRE_TOOLKIT_KEYS, **summary)
        return UpgradeStepOutcome(
            name=STEP_RETIRE_TOOLKIT_KEYS,
            action="performed",
            summary=summary,
            warnings=tuple(warnings),
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

        try:
            result = await ToolkitFlatteningService(self._ctx).run()
        except Exception as exc:
            # Not ledgered: the flatten is idempotent per pair, so the re-run
            # after the fix completes whatever this attempt left undone.
            logger.exception("upgrade_step_failed", step=STEP_FLATTEN_TOOLKITS)
            return UpgradeStepOutcome(
                name=STEP_FLATTEN_TOOLKITS,
                action="failed",
                summary={"error": f"{type(exc).__name__}: {exc}"},
                failed=True,
            )
        review = Counter(
            f.category for f in result.findings if f.category not in _BOOKKEEPING_CATEGORIES
        )
        default_deny = sum(
            1
            for f in result.findings
            if f.category == "binding_created" and f.detail.get("default_deny")
        )
        summary = {
            "pairs_total": result.pairs_total,
            "created": result.created,
            "created_default_deny": default_deny,
            "already_present": result.already_present,
            "findings": dict(sorted(review.items())),
        }
        await self._record(STEP_FLATTEN_TOOLKITS, summary)
        logger.info("upgrade_step", step=STEP_FLATTEN_TOOLKITS, **summary)
        warnings = (
            (
                f"{default_deny} agent-credential pair(s) were bound default-deny (conflicting "
                "or missing toolkit rules); review them with "
                "`jentic_one flatten-toolkits --diff-only --report report.jsonl`",
            )
            if default_deny
            else ()
        )
        return UpgradeStepOutcome(
            name=STEP_FLATTEN_TOOLKITS, action="performed", summary=summary, warnings=warnings
        )

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


def _skipped(name: str, reason: str) -> UpgradeStepOutcome:
    return UpgradeStepOutcome(name=name, action="skipped", summary={"reason": reason})
