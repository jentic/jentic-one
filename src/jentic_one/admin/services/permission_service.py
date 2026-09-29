"""Permission management service."""

from __future__ import annotations

from collections.abc import Sequence

from jentic_one.admin.core.permissions import (
    ALL_PERMISSIONS,
    ORG_ADMIN,
    compute_effective,
)
from jentic_one.admin.repos import UserPermissionGrantRepository, UserRepository
from jentic_one.admin.services._support.user_management import (
    ensure_can_manage,
    ensure_not_last_active_admin,
)
from jentic_one.admin.services.errors import (
    OrgAdminGrantForbiddenError,
    PermissionNotGrantableError,
    UnknownPermissionError,
    UserNotFoundError,
)
from jentic_one.admin.services.schemas.permissions import (
    PermissionCatalogueEntry,
    PermissionsView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.agent_scope_ceiling import is_agent_scope_grantable
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.scopes import RETIRED_SCOPES


class PermissionService:
    """Manages permission catalogue, assignment, and expansion."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_catalogue(self, caller_user_id: str) -> list[PermissionCatalogueEntry]:
        """The permission catalogue as seen by the caller.

        ``grantable_by_caller`` follows the agent scope ceiling
        (``is_agent_scope_grantable``) — the only UI consumer is the agent
        scope picker, and the flag must never offer a scope that
        ``POST /agents`` / ``PUT /agents/{id}/scopes`` would reject.
        """
        caller_effective = await self.get_effective_for_user(caller_user_id)
        caller_effective_set = set(caller_effective.effective)

        entries: list[PermissionCatalogueEntry] = []
        for perm in ALL_PERMISSIONS.values():
            if perm.name == ORG_ADMIN and ORG_ADMIN not in caller_effective_set:
                continue
            grantable = is_agent_scope_grantable(perm.name, caller_effective_set)
            entries.append(
                PermissionCatalogueEntry(
                    name=perm.name,
                    description=perm.description,
                    implies=sorted(perm.implies),
                    grantable_by_caller=grantable,
                )
            )
        return entries

    async def get_assigned_for_user(self, user_id: str) -> list[str]:
        async with self._ctx.admin_db.session() as session:
            grants = await UserPermissionGrantRepository.get_grants_for_user(session, user_id)
        return [g.permission for g in grants]

    async def get_effective_for_user(self, user_id: str) -> PermissionsView:
        assigned = await self.get_assigned_for_user(user_id)
        effective = compute_effective(set(assigned))
        return PermissionsView(assigned=assigned, effective=sorted(effective))

    async def project_for_users(self, user_ids: Sequence[str]) -> dict[str, list[str]]:
        """Batch-fetch assigned permissions for multiple users."""
        if not user_ids:
            return {}
        async with self._ctx.admin_db.session() as session:
            grants = await UserPermissionGrantRepository.list_for_users(session, user_ids)
        result: dict[str, list[str]] = {uid: [] for uid in user_ids}
        for grant in grants:
            result.setdefault(grant.user_id, []).append(grant.permission)
        return result

    async def set_assigned(
        self,
        user_id: str,
        permissions: list[str],
        *,
        identity: Identity,
    ) -> list[str]:
        """Validate and set permissions for a user.

        The caller may only grant permissions they hold (``validate_grants``),
        and may only change the permissions of a user whose current
        permissions they already hold (``org:admin`` and self exempt).
        Removing ``org:admin`` from the last active holder is refused.
        """
        granted_by = identity.sub
        await self.validate_grants(granted_by, permissions)

        async with self._ctx.admin_db.transaction() as session:
            if await UserRepository.get_by_id(session, user_id) is None:
                raise UserNotFoundError(user_id)
            permission_sets = await UserPermissionGrantRepository.get_permission_sets(
                session, [user_id, granted_by]
            )
            ensure_can_manage(user_id, identity, permission_sets)
            previous = sorted(permission_sets[user_id])
            if ORG_ADMIN in permission_sets[user_id] and ORG_ADMIN not in permissions:
                await ensure_not_last_active_admin(session, user_id)

            grants = await UserPermissionGrantRepository.set_permissions(
                session,
                user_id,
                permissions=set(permissions),
                granted_by=granted_by,
                created_by=granted_by,
            )
            previous_set = set(previous)
            new_set = set(permissions)
            added = sorted(new_set - previous_set)
            removed = sorted(previous_set - new_set)

            if added:
                await record_audit(
                    session,
                    action=AuditAction.GRANT,
                    target_type=AuditTargetType.PERMISSION,
                    target_id=user_id,
                    actor_type=identity.actor_type,
                    actor_id=granted_by,
                    before={"permissions": sorted(previous)},
                    after={"permissions": sorted(permissions)},
                    origin=identity.origin.value,
                )
            if removed:
                await record_audit(
                    session,
                    action=AuditAction.REVOKE,
                    target_type=AuditTargetType.PERMISSION,
                    target_id=user_id,
                    actor_type=identity.actor_type,
                    actor_id=granted_by,
                    before={"permissions": sorted(previous)},
                    after={"permissions": sorted(permissions)},
                    reason=f"revoked: {', '.join(removed)}",
                    origin=identity.origin.value,
                )
        return [g.permission for g in grants]

    async def validate_grants(self, granter_user_id: str, permissions: list[str]) -> None:
        """Validate that all permissions exist and the granter can grant them.

        ``RETIRED_SCOPES`` members are accepted and skipped: a stored grant
        set written before a scope retirement (theme-5 Phase 5b) must
        re-submit unchanged without a 422. The retired string is stored
        as-is and grants nothing — Phase 6b sweeps it.
        """
        granter_effective = await self.get_effective_for_user(granter_user_id)
        granter_set = set(granter_effective.effective)

        for perm in permissions:
            if perm in RETIRED_SCOPES:
                continue
            if perm not in ALL_PERMISSIONS:
                raise UnknownPermissionError(perm)
            if perm == ORG_ADMIN and ORG_ADMIN not in granter_set:
                raise OrgAdminGrantForbiddenError()
            if perm not in granter_set:
                raise PermissionNotGrantableError(perm)
