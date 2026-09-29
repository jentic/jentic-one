"""Guards shared by the services that manage *other* users.

Used by ``UserService`` (email change, disable/enable/delete, re-invite) and
``PermissionService.set_assigned`` so both apply one ceiling and one
last-``org:admin`` rule.
"""

from __future__ import annotations

from typing import Any

from jentic_one.admin.core.permissions import ORG_ADMIN, compute_effective
from jentic_one.admin.repos import UserRepository
from jentic_one.admin.services.errors import LastActiveAdminError, UserManagementForbiddenError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.scopes import RETIRED_SCOPES


def ensure_can_manage(
    target_user_id: str, identity: Identity, permission_sets: dict[str, set[str]]
) -> None:
    """Check the caller may change the target's account status or permissions.

    A caller may manage their own account, and an ``org:admin`` caller may
    manage anyone. Otherwise the caller must already hold every permission the
    target holds — the same ceiling ``PermissionService.validate_grants``
    applies to granting — so a ``users:write`` holder cannot lock
    out or strip a more privileged user. Like ``validate_grants``, the
    caller's authority is their live stored grants, not the claims on the
    presented token.

    ``permission_sets`` maps user id -> directly-granted permissions and must
    contain both the target and the caller.
    """
    if target_user_id == identity.sub:
        return
    caller_effective = compute_effective(permission_sets.get(identity.sub, set()))
    if ORG_ADMIN in caller_effective:
        return
    target_assigned = permission_sets.get(target_user_id, set())
    if not compute_effective(target_assigned - RETIRED_SCOPES) <= caller_effective:
        raise UserManagementForbiddenError(target_user_id)


def ensure_can_change_email(
    target_user_id: str, identity: Identity, permission_sets: dict[str, set[str]]
) -> None:
    """Check the caller may change the target's email address.

    Stricter than :func:`ensure_can_manage`: only ``org:admin`` may change
    another user's email, whatever the target holds, because the email is
    what an external IdP sign-in links the account by. Callers may always
    change their own email.
    """
    if target_user_id == identity.sub:
        return
    if ORG_ADMIN not in compute_effective(permission_sets.get(identity.sub, set())):
        raise UserManagementForbiddenError(
            target_user_id, f"Changing the email of user '{target_user_id}' requires org:admin"
        )


async def ensure_not_last_active_admin(session: Any, target_user_id: str) -> None:
    """Raise if removing the target as an active ``org:admin`` would leave none.

    Call inside the transaction that disables, deletes, or strips
    ``org:admin`` from the target. The active holders are read with row locks
    so concurrent removals of different admins cannot both pass the check.
    """
    holders = await UserRepository.lock_active_holders_of(session, ORG_ADMIN)
    if target_user_id in holders and not holders - {target_user_id}:
        raise LastActiveAdminError(target_user_id)
