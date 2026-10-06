"""Mark shared rule sets curated when an admin or a system job created them.

``permission_rule_sets.curated`` is recorded when a set is created
(``CredentialService.create_rule_set``). Sets created without it (before the
column existed, or by an older release running against the newer schema)
start as not curated; :meth:`RuleSetCurationService.mark_existing` marks the
ones whose creator is a system actor (``system:…``, e.g. the toolkit
flattening job) or a user holding ``org:admin``. The creator's permissions
live in the admin database, so this runs as an upgrade step rather than
inside the control Alembic tree, and it runs on every full upgrade.

Creators are judged by the permissions they hold when the step runs; a user
who held ``org:admin`` when creating a set and no longer does is not
recognised. Such a set stays attachable by its creator and ``org:admin``.

:meth:`RuleSetCurationService.mark_existing` also reports every direct
binding attached to a non-curated set whose creator is neither the bound
agent's owner nor the agent itself. The attach gate admits only the set's
creator or an ``org:admin`` for such a set, but an attachment made before
the gate existed stands, and the set's creator can still edit the rules
that govern another user's agent. The run reports these bindings and leaves
them attached; detaching would silently change the agent's effective
policy. The listing is informational: when it cannot run, the result
carries the error instead and the marking stands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from jentic_one.control.repos import PermissionRuleSetRepository
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.shared.context import Context
from jentic_one.shared.scopes import ORG_ADMIN

logger = structlog.get_logger(__name__)

#: ``created_by`` prefix of the system actors that create rule sets.
SYSTEM_CREATOR_PREFIX = "system:"

#: Upper bound on the ids bound into one statement, well under the parameter
#: limits of Postgres (32767) and SQLite (32766).
_BATCH = 1000


@dataclass(frozen=True)
class CrossOwnerAttachment:
    """A binding attached to a non-curated set neither its agent nor the agent's owner created."""

    binding_id: str
    agent_id: str
    agent_name: str
    #: ``None`` for an agent with no owner.
    owner_id: str | None
    credential_id: str
    rule_set_id: str
    rule_set_name: str
    rule_set_creator: str


@dataclass(frozen=True)
class RuleSetCurationResult:
    """What the run marked curated, and the cross-owner attachments still standing."""

    marked: int
    admin_creators: int
    cross_owner: tuple[CrossOwnerAttachment, ...] = ()
    #: Why the cross-owner listing could not run (``cross_owner`` is then empty).
    cross_owner_error: str | None = None


def _batches(items: Sequence[str]) -> list[list[str]]:
    return [list(items[i : i + _BATCH]) for i in range(0, len(items), _BATCH)]


class RuleSetCurationService:
    """Backfills ``curated`` across the control and admin databases."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def mark_existing(self) -> RuleSetCurationResult:
        """Mark curated every non-curated set created by a system actor or an ``org:admin``.

        Idempotent: sets already curated are left alone, so a re-run marks
        only sets created since the previous run. Then lists the cross-owner
        attachments on the sets still not curated (see the module docstring);
        a failure there is reported in the result, never raised.
        """
        async with self._ctx.control_db.session() as session:
            creators = await PermissionRuleSetRepository.list_uncurated_creators(session)
        user_creators = [c for c in creators if not c.startswith(SYSTEM_CREATOR_PREFIX)]
        admins: set[str] = set()
        async with self._ctx.admin_db.session() as session:
            for batch in _batches(user_creators):
                admins |= await PrerequisiteRepository.filter_user_ids_with_permission(
                    session, user_ids=batch, permission=ORG_ADMIN
                )
        marked = 0
        async with self._ctx.control_db.transaction() as session:
            # One batch even with no admin creator, so system-created sets are marked.
            for batch in _batches(sorted(admins)) or [[]]:
                marked += await PermissionRuleSetRepository.mark_curated(
                    session, creators=batch, creator_prefix=SYSTEM_CREATOR_PREFIX
                )
        try:
            cross_owner = await self._cross_owner()
        except Exception as exc:
            logger.exception("rule_set_cross_owner_listing_failed")
            return RuleSetCurationResult(
                marked=marked,
                admin_creators=len(admins),
                cross_owner_error=f"{type(exc).__name__}: {exc}",
            )
        return RuleSetCurationResult(
            marked=marked, admin_creators=len(admins), cross_owner=cross_owner
        )

    async def _cross_owner(self) -> tuple[CrossOwnerAttachment, ...]:
        async with self._ctx.control_db.session() as session:
            uncurated = await PermissionRuleSetRepository.list_uncurated_with_creator(session)
        sets = {set_id: (name, creator) for set_id, name, creator in uncurated}
        if not sets:
            return ()
        found: list[CrossOwnerAttachment] = []
        after_id: str | None = None
        async with self._ctx.admin_db.session() as session:
            while True:
                page = await PrerequisiteRepository.list_rule_set_bindings_page(
                    session, after_id=after_id, limit=_BATCH
                )
                for b in page:
                    entry = sets.get(b.rule_set_id)
                    if entry is None:
                        continue
                    name, creator = entry
                    if creator in (b.owner_id, b.agent_id):
                        continue
                    found.append(
                        CrossOwnerAttachment(
                            binding_id=b.binding_id,
                            agent_id=b.agent_id,
                            agent_name=b.agent_name,
                            owner_id=b.owner_id,
                            credential_id=b.credential_id,
                            rule_set_id=b.rule_set_id,
                            rule_set_name=name,
                            rule_set_creator=creator,
                        )
                    )
                if len(page) < _BATCH:
                    break
                after_id = page[-1].binding_id
        return tuple(found)
