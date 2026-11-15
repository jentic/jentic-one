"""Permission ceiling for granting platform permissions to an agent.

Applied by ``AgentService.create`` (explicit permissions),
``AgentService.approve`` (authored grants on a pending agent) and
``AgentService.replace_permissions``.
The rules:

- Every newly granted permission must be in the permission catalogue
  (:data:`ALL_PERMISSIONS`). Members of :data:`RETIRED_PERMISSIONS` are
  accepted-and-ignored, matching every other permission validation path.
- Which catalogue permissions the caller may grant is
  :func:`~jentic_one.shared.auth.agent_permission_ceiling.is_agent_permission_grantable`
  (``org:admin`` grants anything; anyone else only held-or-default permissions and
  never ``org:admin`` / ``agents:write``) — the same predicate behind the
  ``grantable_by_caller`` flag of ``GET /permissions``.
- Every newly granted non-retired permission must also survive the agent owner's
  ceiling (:func:`~jentic_one.shared.auth.agent_permission_ceiling.cap_agent_permissions`),
  so a grant never exceeds the owner's effective permissions.
- Permissions the agent already holds are not re-checked: re-submitting the current
  set minus one permission is a narrowing, not a grant.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from jentic_one.auth.services.errors import (
    AgentPermissionExceedsOwnerError,
    PermissionNotGrantableError,
    UnknownPermissionError,
)
from jentic_one.shared.auth.agent_permission_ceiling import (
    cap_agent_permissions,
    is_agent_permission_grantable,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    ALL_PERMISSIONS,
    RETIRED_PERMISSIONS,
    compute_effective,
)


def check_agent_permission_grant(
    requested: Iterable[str],
    *,
    identity: Identity,
    already_held: Collection[str] = (),
    owner_effective: Collection[str] | None = None,
) -> None:
    """Raise if ``identity`` may not grant every permission in ``requested`` to an agent.

    Raises ``UnknownPermissionError`` for a permission outside the catalogue,
    ``PermissionNotGrantableError`` for a permission above the caller's ceiling,
    and ``AgentPermissionExceedsOwnerError`` for a permission above the agent
    owner's ceiling.
    """
    held = set(already_held)
    new_permissions = [p for p in dict.fromkeys(requested) if p not in held]
    for permission in new_permissions:
        if permission not in ALL_PERMISSIONS and permission not in RETIRED_PERMISSIONS:
            raise UnknownPermissionError(permission)
    effective = compute_effective(set(identity.permissions))
    non_retired_new = [p for p in new_permissions if p not in RETIRED_PERMISSIONS]
    for permission in non_retired_new:
        if not is_agent_permission_grantable(permission, effective):
            raise PermissionNotGrantableError(permission)
    allowed_by_owner = set(cap_agent_permissions(non_retired_new, owner_effective))
    for permission in non_retired_new:
        if permission not in allowed_by_owner:
            raise AgentPermissionExceedsOwnerError(permission)
