"""Permission management service."""

from __future__ import annotations

from collections.abc import Sequence

from jentic_one.admin.core.permissions import (
    ALL_PERMISSIONS,
    ORG_ADMIN,
    compute_effective,
)
from jentic_one.admin.repos import UserPermissionGrantRepository
from jentic_one.admin.services.errors import (
    OrgAdminGrantForbiddenError,
    PermissionNotGrantableError,
    UnknownPermissionError,
)
from jentic_one.admin.services.schemas.permissions import (
    PermissionCatalogueEntry,
    PermissionsView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context


class PermissionService:
    """Manages permission catalogue, assignment, and expansion."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_catalogue(self, caller_user_id: str) -> list[PermissionCatalogueEntry]:
        caller_effective = await self.get_effective_for_user(caller_user_id)
        caller_effective_set = set(caller_effective.effective)

        entries: list[PermissionCatalogueEntry] = []
        for perm in ALL_PERMISSIONS.values():
            if perm.name == ORG_ADMIN and ORG_ADMIN not in caller_effective_set:
                continue
            grantable = perm.name in caller_effective_set
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

    async def get_effective_for_service_account(self, service_account_id: str) -> PermissionsView:
        # Right now, ServiceAccounts might be using ActorScopeGrantRepository
        # or a similar mechanism, but for now they don't have explicit grants
        # configured through this service yet. We return an empty view.
        # In the future, we will fetch directly from a ServiceAccountPermissionGrant table
        # or use ActorScopeGrant as permissions for SAs.
        return PermissionsView(assigned=[], effective=[])

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
        """Validate and set permissions for a user."""
        granted_by = identity.sub
        await self.validate_grants(granted_by, permissions)

        previous = await self.get_assigned_for_user(user_id)

        async with self._ctx.admin_db.transaction() as session:
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
        """Validate that all permissions exist and the granter can grant them."""
        granter_effective = await self.get_effective_for_user(granter_user_id)
        granter_set = set(granter_effective.effective)

        for perm in permissions:
            if perm not in ALL_PERMISSIONS:
                raise UnknownPermissionError(perm)
            if perm == ORG_ADMIN and ORG_ADMIN not in granter_set:
                raise OrgAdminGrantForbiddenError()
            if perm not in granter_set:
                raise PermissionNotGrantableError(perm)
