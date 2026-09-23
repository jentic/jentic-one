"""Conceptual permission catalogue — the *meaning* of every permission.

The endpoint reference (:mod:`jentic_one.shared.web.endpoint_reference`) answers
"which permission does this endpoint need?". This module answers the
complementary, conceptual question the docs SPA needs: "what does each permission
*mean*, and how do permissions relate to one another?".

It is built directly from
:data:`jentic_one.shared.auth.permission_catalog.ALL_PERMISSIONS` (the single
source of truth), so the catalogue can never drift from the enforced permission
set. For each permission it exposes:

- ``description`` — the human-readable meaning (from the ``Permission`` entry).
- ``family`` — the resource prefix (``agents``, ``credentials``, ``owner`` …),
  derived from the permission string, used to group the visual tree.
- ``action`` — the verb suffix (``read`` / ``write`` / ``execute`` / ``admin``).
- ``implies`` — the *direct* child permissions (one hop).
- ``implies_transitive`` — the full transitive closure (sorted), so the UI can
  show "holding this grants …" without re-deriving the graph client-side.

The payload is folded into ``GET /reference/endpoints.json`` (and the committed
``docs/reference/endpoints.json``) so the SPA fetches one document.
"""

from __future__ import annotations

from typing import Any

from jentic_one.shared.auth.permission_catalog import (
    ALL_PERMISSIONS,
    ORG_ADMIN,
    compute_implies_transitive,
)

#: Schema identifier for the permission-catalogue section (bump on a breaking
#: change).
PERMISSION_CATALOG_SCHEMA = "jentic.permission-catalog/v1"

#: Human-readable label for each known family prefix. Anything not listed falls
#: back to a title-cased version of the prefix, so a new family still renders.
_FAMILY_LABELS: dict[str, str] = {
    "org": "Organisation",
    "capabilities": "Capabilities",
    "apis": "APIs",
    "catalog": "Catalog",
    "overlays": "Overlays",
    "credentials": "Credentials",
    "agents": "Agents",
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
    "apis": "Imported API definitions and metadata.",
    "catalog": "Importing public catalog APIs into the local registry.",
    "overlays": "Confirming spec overlays — rewrites the API's served revision.",
    "credentials": "Stored credential metadata and lifecycle.",
    "agents": "Agent identities and their configuration.",
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
    "apis",
    "catalog",
    "overlays",
    "credentials",
    "agents",
    "users",
    "jobs",
    "events",
    "executions",
    "audit",
    "owner",
)


def _family_of(permission: str) -> str:
    """The resource-family prefix of a permission (the part before the first ``:``)."""
    return permission.split(":", 1)[0]


def _action_of(permission: str) -> str:
    """The action suffix of a permission (``read`` / ``write`` / ``execute`` / ``admin``).

    For ``owner:<resource>:read`` style permissions the trailing segment is the
    action; for ``org:admin`` it is ``admin``; for ``agents:read`` it is ``read``.
    """
    return permission.rsplit(":", 1)[-1]


def build_permission_catalog() -> dict[str, Any]:
    """Build the conceptual permission catalogue from the source of truth.

    Returns a JSON-serialisable dict with a ``schema`` marker, the ordered list
    of ``families`` (each with its permissions), and a flat ``permissions`` list
    (so a consumer can index by name without walking families).
    """
    permissions: list[dict[str, Any]] = []
    for name, perm in ALL_PERMISSIONS.items():
        family = _family_of(name)
        permissions.append(
            {
                "name": name,
                "description": perm.description,
                "family": family,
                "action": _action_of(name),
                "implies": sorted(perm.implies),
                "implies_transitive": sorted(compute_implies_transitive(name)),
                # org:admin is also a hard runtime superpower (deps.py short-circuit),
                # so flag it: it can reach every permission-gated endpoint regardless
                # of the literal implication graph.
                "is_superuser": name == ORG_ADMIN,
            }
        )

    by_family: dict[str, list[dict[str, Any]]] = {}
    for permission in permissions:
        by_family.setdefault(permission["family"], []).append(permission)

    def family_sort_key(fam: str) -> int:
        return _FAMILY_ORDER.index(fam) if fam in _FAMILY_ORDER else len(_FAMILY_ORDER)

    families: list[dict[str, Any]] = []
    for fam in sorted(by_family, key=family_sort_key):
        members = sorted(by_family[fam], key=lambda p: (p["action"] != "admin", p["name"]))
        families.append(
            {
                "name": fam,
                "label": _FAMILY_LABELS.get(fam, fam.replace("-", " ").title()),
                "blurb": _FAMILY_BLURBS.get(fam, ""),
                "permissions": members,
            }
        )

    return {
        "schema": PERMISSION_CATALOG_SCHEMA,
        "total": len(permissions),
        "families": families,
        "permissions": sorted(permissions, key=lambda p: p["name"]),
    }
