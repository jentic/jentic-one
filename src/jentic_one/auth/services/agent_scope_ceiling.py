"""Scope ceiling for granting platform scopes to an agent.

Applied by ``AgentService.create`` (explicit scopes) and
``AgentService.replace_scopes``. The rules:

- Every newly granted scope must be in the permission catalogue
  (:data:`ALL_PERMISSIONS`). Members of :data:`RETIRED_SCOPES` are
  accepted-and-ignored, matching every other scope validation path.
- A caller holding ``org:admin`` may grant any catalogue scope.
- Any other caller may grant only scopes in its own effective permission set
  (the implication-expanded ``identity.permissions``) or in the default agent
  baseline (:data:`DEFAULT_AGENT_SCOPES` — what an agent gets when created
  without explicit scopes or approved from self-registration), and never
  ``org:admin`` or ``agents:write``, even if it holds them.
- Scopes the agent already holds are not re-checked: re-submitting the current
  set minus one scope is a narrowing, not a grant.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from jentic_one.auth.services.errors import ScopeNotGrantableError, UnknownScopeError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, compute_effective
from jentic_one.shared.scopes import (
    AGENTS_WRITE,
    DEFAULT_AGENT_SCOPES,
    ORG_ADMIN,
    RETIRED_SCOPES,
)

#: Scopes only an ``org:admin`` caller may put on an agent.
ADMIN_ONLY_AGENT_SCOPES: frozenset[str] = frozenset({ORG_ADMIN, AGENTS_WRITE})


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
    if ORG_ADMIN in identity.permissions:
        return
    effective = compute_effective(set(identity.permissions))
    for scope in new_scopes:
        if scope in RETIRED_SCOPES:
            continue
        if scope in ADMIN_ONLY_AGENT_SCOPES:
            raise ScopeNotGrantableError(scope)
        if scope not in effective and scope not in DEFAULT_AGENT_SCOPES:
            raise ScopeNotGrantableError(scope)
