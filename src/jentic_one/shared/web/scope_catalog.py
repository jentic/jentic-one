"""Conceptual scope catalogue — the *meaning* of every permission scope.

The endpoint reference (:mod:`jentic_one.shared.web.endpoint_reference`) answers
"which scope does this endpoint need?". This module answers the complementary,
conceptual question the docs SPA needs: "what does each scope *mean*, and how do
scopes relate to one another?".

It is built directly from :data:`jentic_one.admin.core.permissions.ALL_PERMISSIONS`
(the single source of truth), so the catalogue can never drift from the
enforced permission set. For each scope it exposes:

- ``description`` — the human-readable meaning (from the ``Permission`` entry).
- ``family`` — the resource prefix (``agents``, ``credentials``, ``owner`` …),
  derived from the scope string, used to group the visual tree.
- ``action`` — the verb suffix (``read`` / ``write`` / ``execute`` / ``admin``).
- ``implies`` — the *direct* child scopes (one hop).
- ``implies_transitive`` — the full transitive closure (sorted), so the UI can
  show "holding this grants …" without re-deriving the graph client-side.

The payload is folded into ``GET /reference/endpoints.json`` (and the committed
``docs/reference/endpoints.json``) so the SPA fetches one document.
"""

from __future__ import annotations

from typing import Any

from jentic_one.admin.core.permissions import (
    ALL_PERMISSIONS,
    ORG_ADMIN,
    compute_implies_transitive,
)

#: Schema identifier for the scope-catalogue section (bump on a breaking change).
SCOPE_CATALOG_SCHEMA = "jentic.scope-catalog/v1"

#: Human-readable label for each known family prefix. Anything not listed falls
#: back to a title-cased version of the prefix, so a new family still renders.
_FAMILY_LABELS: dict[str, str] = {
    "org": "Organisation",
    "capabilities": "Capabilities",
    "toolkits": "Toolkits",
    "apis": "APIs",
    "catalog": "Catalog",
    "credentials": "Credentials",
    "agents": "Agents",
    "service-accounts": "Service accounts",
    "users": "Users",
    "jobs": "Jobs",
    "events": "Events",
    "executions": "Executions",
    "audit": "Audit",
    "owner": "Owner-scoped reads",
}

#: One-line description of what each family governs (shown on the family header).
_FAMILY_BLURBS: dict[str, str] = {
    "org": "Organisation-wide administration.",
    "capabilities": "Discovering and executing capabilities through the broker.",
    "toolkits": "Toolkit configuration and lifecycle.",
    "apis": "Imported API definitions and metadata.",
    "catalog": "Importing public catalog APIs into the local registry.",
    "credentials": "Stored credential metadata and lifecycle.",
    "agents": "Agent identities and their configuration.",
    "service-accounts": "Non-human service-account identities.",
    "users": "Human user accounts and their permissions.",
    "jobs": "Asynchronous background jobs.",
    "events": "Platform events.",
    "executions": "Execution (broker call) records.",
    "audit": "The audit log.",
    "owner": (
        "Read access an agent has to the resources owned by the human who "
        "created it. Granted to agents by default; never grants write."
    ),
}

#: Display order for families in the visual tree (admin first, owner reads last).
_FAMILY_ORDER: tuple[str, ...] = (
    "org",
    "capabilities",
    "toolkits",
    "apis",
    "catalog",
    "credentials",
    "agents",
    "service-accounts",
    "users",
    "jobs",
    "events",
    "executions",
    "audit",
    "owner",
)


def _family_of(scope: str) -> str:
    """The resource-family prefix of a scope (the part before the first ``:``)."""
    return scope.split(":", 1)[0]


def _action_of(scope: str) -> str:
    """The action suffix of a scope (``read`` / ``write`` / ``execute`` / ``admin``).

    For ``owner:<resource>:read`` style scopes the trailing segment is the
    action; for ``org:admin`` it is ``admin``; for ``agents:read`` it is ``read``.
    """
    return scope.rsplit(":", 1)[-1]


def build_scope_catalog() -> dict[str, Any]:
    """Build the conceptual scope catalogue from the permission source of truth.

    Returns a JSON-serialisable dict with a ``schema`` marker, the ordered list
    of ``families`` (each with its scopes), and a flat ``scopes`` list (so a
    consumer can index by name without walking families).
    """
    scopes: list[dict[str, Any]] = []
    for name, perm in ALL_PERMISSIONS.items():
        family = _family_of(name)
        scopes.append(
            {
                "name": name,
                "description": perm.description,
                "family": family,
                "action": _action_of(name),
                "implies": sorted(perm.implies),
                "implies_transitive": sorted(compute_implies_transitive(name)),
                # org:admin is also a hard runtime superpower (deps.py short-circuit),
                # so flag it: it can reach every scope-gated endpoint regardless of
                # the literal implication graph.
                "is_superuser": name == ORG_ADMIN,
            }
        )

    by_family: dict[str, list[dict[str, Any]]] = {}
    for scope in scopes:
        by_family.setdefault(scope["family"], []).append(scope)

    def family_sort_key(fam: str) -> int:
        return _FAMILY_ORDER.index(fam) if fam in _FAMILY_ORDER else len(_FAMILY_ORDER)

    families: list[dict[str, Any]] = []
    for fam in sorted(by_family, key=family_sort_key):
        members = sorted(by_family[fam], key=lambda s: (s["action"] != "admin", s["name"]))
        families.append(
            {
                "name": fam,
                "label": _FAMILY_LABELS.get(fam, fam.replace("-", " ").title()),
                "blurb": _FAMILY_BLURBS.get(fam, ""),
                "scopes": members,
            }
        )

    return {
        "schema": SCOPE_CATALOG_SCHEMA,
        "total": len(scopes),
        "families": families,
        "scopes": sorted(scopes, key=lambda s: s["name"]),
    }
