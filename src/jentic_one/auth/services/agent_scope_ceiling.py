"""Scope ceiling for granting platform scopes to an agent.

Applied by ``AgentService.create`` (explicit scopes), ``AgentService.approve``
(scopes a self-registration requested) and ``AgentService.replace_scopes``.
The rules:

- Every newly granted scope must be in the permission catalogue
  (:data:`ALL_PERMISSIONS`). Members of :data:`RETIRED_SCOPES` are
  accepted-and-ignored, matching every other scope validation path.
- Which catalogue scopes the caller may grant is
  :func:`~jentic_one.shared.auth.agent_scope_ceiling.is_agent_scope_grantable`
  (``org:admin`` grants anything; anyone else only held-or-default scopes and
  never ``org:admin`` / ``agents:write``) — the same predicate behind the
  ``grantable_by_caller`` flag of ``GET /permissions``.
- Scopes the agent already holds are not re-checked: re-submitting the current
  set minus one scope is a narrowing, not a grant.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from jentic_one.auth.services.errors import ScopeNotGrantableError, UnknownScopeError
from jentic_one.shared.auth.agent_scope_ceiling import is_agent_scope_grantable
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, compute_effective
from jentic_one.shared.scopes import RETIRED_SCOPES


def check_agent_scope_grant(
    requested: Iterable[str],
    *,
    identity: Identity,
    already_held: Collection[str] = (),
) -> None:
    """Raise if ``identity`` may not grant every scope in ``requested`` to an agent.

    Raises ``UnknownScopeError`` for a scope outside the catalogue and
    ``ScopeNotGrantableError`` for a scope above the caller's ceiling.
    """
    held = set(already_held)
    new_scopes = [s for s in dict.fromkeys(requested) if s not in held]
    for scope in new_scopes:
        if scope not in ALL_PERMISSIONS and scope not in RETIRED_SCOPES:
            raise UnknownScopeError(scope)
    effective = compute_effective(set(identity.permissions))
    for scope in new_scopes:
        if scope in RETIRED_SCOPES:
            continue
        if not is_agent_scope_grantable(scope, effective):
            raise ScopeNotGrantableError(scope)
