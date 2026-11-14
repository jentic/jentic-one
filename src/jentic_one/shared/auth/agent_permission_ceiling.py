"""Agent permission ceiling: which platform permissions a caller may grant to an agent.

Tier-neutral so both sides of the rule share one predicate:

- ``auth`` enforces it on agent create / approve / replace-permissions
  (``jentic_one.auth.services.agent_permission_ceiling``);
- ``admin`` reflects it in the ``grantable_by_caller`` flag of
  ``GET /permissions`` so the UI never offers a permission the server rejects.

The rule, for a permission in the permission catalogue:

- a caller holding ``org:admin`` may grant any permission;
- any other caller may grant a permission in its own effective (implication-expanded)
  permission set or in the default agent baseline
  (:data:`~jentic_one.shared.auth.permission_catalog.DEFAULT_AGENT_PERMISSIONS`), and never
  ``org:admin`` or ``agents:write``, even if it holds them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from jentic_one.shared.auth.permission_catalog import (
    AGENTS_WRITE,
    DEFAULT_AGENT_PERMISSIONS,
    ORG_ADMIN,
)

#: Permissions only an ``org:admin`` caller may put on an agent.
ADMIN_ONLY_AGENT_PERMISSIONS: frozenset[str] = frozenset({ORG_ADMIN, AGENTS_WRITE})


def is_agent_permission_grantable(permission: str, caller_effective: Collection[str]) -> bool:
    """Whether a caller with ``caller_effective`` permissions may grant ``permission`` to an agent.

    ``caller_effective`` must already be implication-expanded. Catalogue
    membership is the caller's concern (unknown permissions are a separate error).
    """
    if ORG_ADMIN in caller_effective:
        return True
    if permission in ADMIN_ONLY_AGENT_PERMISSIONS:
        return False
    return permission in caller_effective or permission in DEFAULT_AGENT_PERMISSIONS


def cap_agent_permissions(
    agent_grants: Iterable[str], owner_effective: Collection[str] | None
) -> list[str]:
    """Cap ``agent_grants`` by the agent owner's live ``owner_effective`` permissions.

    Returns ``agent_grants & (owner_effective | DEFAULT_AGENT_PERMISSIONS)`` in
    grant order (deduplicated). When the agent is ownerless
    (``owner_effective is None``) or the owner holds ``org:admin``, the grants
    are returned unchanged. ``ADMIN_ONLY_AGENT_PERMISSIONS`` survive only when
    the owner holds ``org:admin`` (or the agent is ownerless).
    ``owner_effective`` must already be implication-expanded.
    """
    grants = list(dict.fromkeys(agent_grants))
    if owner_effective is None or ORG_ADMIN in owner_effective:
        return grants
    return [p for p in grants if is_agent_permission_grantable(p, owner_effective)]
