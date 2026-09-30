"""Agent scope ceiling: which platform scopes a caller may grant to an agent.

Tier-neutral so both sides of the rule share one predicate:

- ``auth`` enforces it on agent create / approve / replace-scopes
  (``jentic_one.auth.services.agent_scope_ceiling``);
- ``admin`` reflects it in the ``grantable_by_caller`` flag of
  ``GET /permissions`` so the UI never offers a scope the server rejects.

The rule, for a scope in the permission catalogue:

- a caller holding ``org:admin`` may grant any scope;
- any other caller may grant a scope in its own effective (implication-expanded)
  permission set or in the default agent baseline
  (:data:`~jentic_one.shared.scopes.DEFAULT_AGENT_SCOPES`), and never
  ``org:admin`` or ``agents:write``, even if it holds them.
"""

from __future__ import annotations

from collections.abc import Collection

from jentic_one.shared.auth.permission_catalog import AGENTS_WRITE, ORG_ADMIN
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES

#: Scopes only an ``org:admin`` caller may put on an agent.
ADMIN_ONLY_AGENT_SCOPES: frozenset[str] = frozenset({ORG_ADMIN, AGENTS_WRITE})


def is_agent_scope_grantable(scope: str, caller_effective: Collection[str]) -> bool:
    """Whether a caller with ``caller_effective`` permissions may grant ``scope`` to an agent.

    ``caller_effective`` must already be implication-expanded. Catalogue
    membership is the caller's concern (unknown scopes are a separate error).
    """
    if ORG_ADMIN in caller_effective:
        return True
    if scope in ADMIN_ONLY_AGENT_SCOPES:
        return False
    return scope in caller_effective or scope in DEFAULT_AGENT_SCOPES
