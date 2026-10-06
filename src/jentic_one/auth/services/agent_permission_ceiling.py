"""Permission ceiling for granting platform permissions to an agent.

Applied by ``AgentService.create`` (explicit permissions),
``AgentService.approve`` (permissions a self-registration requested) and
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
- Permissions the agent already holds are not re-checked: re-submitting the current
  set minus one permission is a narrowing, not a grant.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from jentic_one.auth.services.errors import PermissionNotGrantableError, UnknownPermissionError
from jentic_one.shared.auth.agent_permission_ceiling import is_agent_permission_grantable
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
) -> None:
    """Raise if ``identity`` may not grant every permission in ``requested`` to an agent.

    Raises ``UnknownPermissionError`` for a permission outside the catalogue and
    ``PermissionNotGrantableError`` for a permission above the caller's ceiling.
    """
    held = set(already_held)
    new_permissions = [p for p in dict.fromkeys(requested) if p not in held]
    for permission in new_permissions:
        if permission not in ALL_PERMISSIONS and permission not in RETIRED_PERMISSIONS:
            raise UnknownPermissionError(permission)
    effective = compute_effective(set(identity.permissions))
    for permission in new_permissions:
        if permission in RETIRED_PERMISSIONS:
            continue
        if not is_agent_permission_grantable(permission, effective):
            raise PermissionNotGrantableError(permission)
