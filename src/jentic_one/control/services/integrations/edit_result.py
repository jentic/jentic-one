"""Normalizing connect-session revision results for edit reviews.

A create session confirmed onto an existing credential transitions to the edit
intent while keeping the create-shaped revision the approver attested. This
module shapes that revision's ``result`` into the full edit result structure
expected by ``EditReview``, ``widens_access``, ``scopes_changed`` and
``EditResultAccess``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jentic_one.control.services.integrations.edits import target_rules


def normalize_edit_result(baseline: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``result`` with every field an edit review requires.

    An edit-shaped ``result`` (from ``resolve_edit``) comes back as an
    unchanged shallow copy. A create-shaped ``result`` (from
    ``create_revision``) fills ``target`` and ``suspended`` from ``baseline``,
    defaults ``scope_notes`` to ``{}``, ensures each rule carries ``id``, and
    unions ``scopes`` with ``baseline["granted_scopes"]`` to match the scope
    set applied on confirmation.
    """
    raw_rules = result.get("rules", [])
    if (
        "target" in result
        and "scope_notes" in result
        and "suspended" in result
        and all("id" in r for r in raw_rules)
    ):
        return dict(result)

    default_target, _ = target_rules(baseline)
    rules = [{"id": r.get("id"), **r} for r in raw_rules]
    if "target" in result:
        scopes = list(result.get("scopes") or [])
    else:
        scopes = sorted(set(baseline.get("granted_scopes") or []) | set(result.get("scopes") or []))
    scope_notes = dict(result["scope_notes"]) if "scope_notes" in result else {}
    suspended = (
        bool(result["suspended"])
        if "suspended" in result
        else bool(baseline.get("suspended", False))
    )
    return {
        **result,
        "target": result.get("target", default_target),
        "rules": rules,
        "scopes": scopes,
        "scope_notes": scope_notes,
        "suspended": suspended,
    }
