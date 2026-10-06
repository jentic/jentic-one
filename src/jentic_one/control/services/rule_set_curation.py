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
binding attached to a non-curated set whose creator is not the bound agent's
owner. The attach gate admits only the set's creator or an ``org:admin``
for such a set, but an attachment made before the gate existed stands, and
the set's creator can still edit the rules that govern another user's
agent. The run reports these bindings and leaves them attached; detaching
would silently change the agent's effective policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from jentic_one.control.repos import PermissionRuleSetRepository
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.shared.context import Context
from jentic_one.shared.scopes import ORG_ADMIN

#: ``created_by`` prefix of the system actors that create rule sets.
SYSTEM_CREATOR_PREFIX = "system:"


@dataclass(frozen=True)
class CrossOwnerAttachment:
    """A binding attached to a non-curated set its agent's owner did not create."""

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


class RuleSetCurationService:
    """Backfills ``curated`` across the control and admin databases."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def mark_existing(self) -> RuleSetCurationResult:
        """Mark curated every non-curated set created by a system actor or an ``org:admin``.

        Idempotent: sets already curated are left alone, so a re-run marks
        only sets created since the previous run. Then lists the cross-owner
        attachments on the sets still not curated (see the module docstring).
        """
        async with self._ctx.control_db.session() as session:
            creators = await PermissionRuleSetRepository.list_uncurated_creators(session)
        user_creators = [c for c in creators if not c.startswith(SYSTEM_CREATOR_PREFIX)]
        async with self._ctx.admin_db.session() as session:
            admins = await PrerequisiteRepository.filter_user_ids_with_permission(
                session, user_ids=user_creators, permission=ORG_ADMIN
            )
        async with self._ctx.control_db.transaction() as session:
            marked = await PermissionRuleSetRepository.mark_curated(
                session, creators=admins, creator_prefix=SYSTEM_CREATOR_PREFIX
            )
        return RuleSetCurationResult(
            marked=marked, admin_creators=len(admins), cross_owner=await self._cross_owner()
        )

    async def _cross_owner(self) -> tuple[CrossOwnerAttachment, ...]:
        async with self._ctx.control_db.session() as session:
            uncurated = await PermissionRuleSetRepository.list_uncurated_with_creator(session)
        sets = {set_id: (name, creator) for set_id, name, creator in uncurated}
        async with self._ctx.admin_db.session() as session:
            bindings = await PrerequisiteRepository.list_bindings_for_rule_sets(session, list(sets))
        found: list[CrossOwnerAttachment] = []
        for b in bindings:
            name, creator = sets[b.rule_set_id]
            if creator == b.owner_id:
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
        return tuple(found)
