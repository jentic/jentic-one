"""Unit tests for normalizing connect-session revision results (``integrations/edit_result.py``)."""

from __future__ import annotations

import json
from typing import Any

from jentic_one.control.services.integrations.edit_result import normalize_edit_result
from jentic_one.control.services.integrations.edits import (
    TARGET_BINDING,
    TARGET_RULE_SET,
    EditRequest,
    RuleAdd,
    added_scopes,
    removed_scopes,
    resolve_edit,
    scopes_changed,
    widens_access,
)
from jentic_one.control.services.integrations.revisions import create_revision
from jentic_one.control.web.schemas.integrations import EditResultAccess

_READ = {"effect": "allow", "methods": ["GET"], "path": "/items", "match_mode": "exact"}
_WRITE = {"effect": "allow", "methods": ["POST"], "path": "/items", "match_mode": "exact"}


def _baseline(
    *,
    rules: list[dict[str, Any]] | None = None,
    rule_set: dict[str, Any] | None = None,
    suspended: bool = False,
    granted_scopes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rules": [] if rules is None else rules,
        "rule_set": rule_set,
        "suspended": suspended,
        "granted_scopes": ["read"] if granted_scopes is None else granted_scopes,
    }


def test_create_shaped_result_fills_defaults_and_validates() -> None:
    """Create-shaped results fill target, suspended, scope_notes, rule ids, and unioned scopes."""
    base = _baseline(granted_scopes=["read"])
    revision = create_revision(
        permission_rules=[_READ],
        scopes=["write"],
        reason="need write",
        actor="agnt_1",
    )

    normalized = normalize_edit_result(base, revision.result)

    assert normalized["target"] == TARGET_BINDING
    assert normalized["suspended"] is False
    assert normalized["scope_notes"] == {}
    assert normalized["scopes"] == ["read", "write"]
    assert normalized["rules"] == [{**_READ, "id": None, "added_by": "agnt_1"}]

    validated = EditResultAccess.model_validate(normalized)
    assert validated.target == "binding"
    assert validated.suspended is False
    assert validated.scopes == ["read", "write"]
    assert validated.scope_notes == {}
    assert len(validated.rules) == 1
    assert validated.rules[0].id is None
    assert validated.rules[0].added_by == "agnt_1"


def test_create_shaped_result_uses_rule_set_target_and_suspended_from_baseline() -> None:
    """Baseline rule_set and suspended flags propagate into the normalized result."""
    rule_set = {
        "id": "prs_1",
        "name": "shared",
        "curated": False,
        "rules": [{"id": "prr_1", **_READ, "operations": None, "comment": None}],
    }
    base = _baseline(rule_set=rule_set, suspended=True, granted_scopes=["read"])
    revision = create_revision(permission_rules=[], scopes=["read", "write"], reason=None)

    normalized = normalize_edit_result(base, revision.result)

    assert normalized["target"] == TARGET_RULE_SET
    assert normalized["suspended"] is True
    assert normalized["rules"] == []
    assert normalized["scopes"] == ["read", "write"]
    EditResultAccess.model_validate(normalized)


def test_edit_shaped_result_is_byte_identical_and_idempotent() -> None:
    """An already edit-shaped result is returned unchanged without modifying scopes or notes."""
    existing_rule = {"id": "apr_1", **_READ, "operations": None, "comment": None}
    base = _baseline(rules=[existing_rule], granted_scopes=["read", "legacy"])
    edit_result, _ = resolve_edit(
        base,
        EditRequest(
            add=[RuleAdd(_WRITE)],
            remove_scopes=["legacy"],
            add_scopes=["write"],
            scope_comments={"write": "for writes"},
        ),
        actor="agnt_1",
    )

    normalized = normalize_edit_result(base, edit_result)

    assert normalized == edit_result
    assert normalized is not edit_result
    assert json.dumps(normalized, sort_keys=True) == json.dumps(edit_result, sort_keys=True)
    assert normalize_edit_result(base, normalized) == edit_result


def test_revision_keys_override_baseline_defaults() -> None:
    """Explicit keys on the result override baseline defaults when normalizing partial inputs."""
    base = _baseline(suspended=False, granted_scopes=["read"])
    partial = {
        "target": TARGET_RULE_SET,
        "rules": [{**_READ, "added_by": "agnt_1"}],
        "scopes": ["write"],
        "scope_notes": {"write": {"added_by": "agnt_1", "comment": "custom"}},
        "suspended": True,
    }

    normalized = normalize_edit_result(base, partial)

    assert normalized["target"] == TARGET_RULE_SET
    assert normalized["suspended"] is True
    assert normalized["scopes"] == ["write"]
    assert normalized["scope_notes"] == {"write": {"added_by": "agnt_1", "comment": "custom"}}
    assert normalized["rules"][0]["id"] is None
    EditResultAccess.model_validate(normalized)


def test_normalized_create_shaped_result_never_reports_phantom_scope_removals() -> None:
    """Create-shaped scopes union with baseline granted_scopes so existing scopes stay intact."""
    base = _baseline(granted_scopes=["profile", "read"])
    asking_subset = create_revision(permission_rules=[], scopes=["read"], reason=None)

    normalized_subset = normalize_edit_result(base, asking_subset.result)

    assert normalized_subset["scopes"] == ["profile", "read"]
    assert removed_scopes(base, normalized_subset) == []
    assert added_scopes(base, normalized_subset) == []
    assert scopes_changed(base, normalized_subset) is False
    assert widens_access(base, normalized_subset) is False

    asking_new = create_revision(permission_rules=[], scopes=["write"], reason=None)
    normalized_new = normalize_edit_result(base, asking_new.result)

    assert normalized_new["scopes"] == ["profile", "read", "write"]
    assert removed_scopes(base, normalized_new) == []
    assert added_scopes(base, normalized_new) == ["write"]
    assert scopes_changed(base, normalized_new) is True
    assert widens_access(base, normalized_new) is True
