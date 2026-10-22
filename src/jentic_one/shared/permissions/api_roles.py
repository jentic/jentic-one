"""Which API of a CLI profile a permission rule applies to.

A profile-scoped credential (one connect, one secret) covers several APIs of
one vendor, each with a role: ``rest`` and ``uploads`` (HTTP APIs, path
rules), ``graphql`` (GraphQL top-level-field rules) and ``git`` (git
repository/ref rules). Each rule on such a credential's binding applies to the
API of one role only: the role stored on the rule (``api_role``), or, when the
rule names none, the role its kind implies (:func:`default_api_role`).

On any other credential the role is ignored: a rule's ``match_mode`` alone
decides what it can match.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

from typing import Literal

from jentic_one.shared.permissions.git_rules import GIT_MATCH_MODE
from jentic_one.shared.permissions.graphql_rules import GRAPHQL_MATCH_MODE

ApiRoleName = Literal["rest", "graphql", "git", "uploads"]
"""The API roles a rule can name (the CLI profile manifest's ``ApiRole`` values)."""

API_ROLES: tuple[ApiRoleName, ...] = ("rest", "graphql", "git", "uploads")
"""Every role, in the Literal's order (stable for OpenAPI)."""


#: The rule ``match_mode`` values the API of each role accepts.
ROLE_MATCH_MODES: dict[str, frozenset[str]] = {
    "rest": frozenset({"regex", "prefix", "exact"}),
    "uploads": frozenset({"regex", "prefix", "exact"}),
    "graphql": frozenset({GRAPHQL_MATCH_MODE}),
    "git": frozenset({GIT_MATCH_MODE}),
}


def default_api_role(match_mode: str | None) -> ApiRoleName:
    """The role a rule that names none applies to: ``git``, ``graphql``, else ``rest``."""
    if match_mode == GIT_MATCH_MODE:
        return "git"
    if match_mode == GRAPHQL_MATCH_MODE:
        return "graphql"
    return "rest"


def effective_api_role(api_role: str | None, match_mode: str | None) -> str:
    """The role a stored rule applies to on a profile-scoped credential."""
    return api_role if api_role else default_api_role(match_mode)


__all__ = [
    "API_ROLES",
    "ROLE_MATCH_MODES",
    "ApiRoleName",
    "default_api_role",
    "effective_api_role",
]
