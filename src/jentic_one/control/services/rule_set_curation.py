"""Mark pre-existing shared rule sets curated when an admin or a system job created them.

``permission_rule_sets.curated`` is recorded when a set is created
(``CredentialService.create_rule_set``). Sets created before the column
existed start as not curated; :meth:`RuleSetCurationService.mark_existing`
marks the ones whose creator is a system actor (``system:…``, e.g. the
toolkit flattening job) or a user holding ``org:admin``. The creator's
permissions live in the admin database, so this runs as an upgrade step
rather than inside the control Alembic tree.

Creators are judged by the permissions they hold when the step runs; a user
who held ``org:admin`` when creating a set and no longer does is not
recognised. Such a set stays attachable by its creator and ``org:admin``.
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
class RuleSetCurationResult:
    """How many sets the run marked curated, and how many admin creators it found."""

    marked: int
    admin_creators: int


class RuleSetCurationService:
    """Backfills ``curated`` across the control and admin databases."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def mark_existing(self) -> RuleSetCurationResult:
        """Mark curated every non-curated set created by a system actor or an ``org:admin``.

        Idempotent: sets already curated are left alone, so a re-run marks
        nothing new.
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
        return RuleSetCurationResult(marked=marked, admin_creators=len(admins))
