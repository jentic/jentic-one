"""Resolving an edit request against a binding's baseline (``integrations/edits.py``)."""

from __future__ import annotations

from typing import Any

import pytest

from jentic_one.control.services.integrations.edits import (
    TARGET_BINDING,
    TARGET_RULE_SET,
    EditRequest,
    RuleAdd,
    added_scopes,
    baseline_matches,
    removed_scopes,
    require_change,
    resolve_edit,
    scopes_changed,
    widens_access,
)
from jentic_one.control.services.integrations.errors import (
    NothingToChangeError,
    ScopesNotSupportedError,
    UnknownRuleError,
)

_READ = {"effect": "allow", "methods": ["GET"], "path": "/repos", "match_mode": "prefix"}
_DENY = {"effect": "deny", "methods": ["DELETE"], "path": "/", "match_mode": "prefix"}
_SEND = {"effect": "allow", "methods": ["POST"], "path": "/send", "match_mode": "exact"}


def _rule(rule_id: str, rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule_id,
        "effect": rule["effect"],
        "methods": rule.get("methods"),
        "path": rule.get("path"),
        "match_mode": rule.get("match_mode"),
        "operations": None,
        "comment": None,
    }


def _baseline(*, suspended: bool = False, rule_set: Any = None) -> dict[str, Any]:
    return {
        "rules": [_rule("apr_deny", _DENY), _rule("apr_read", _READ)],
        "rule_set": rule_set,
        "suspended": suspended,
        "granted_scopes": ["repo"],
    }


def _paths(result: dict[str, Any]) -> list[str | None]:
    return [r["path"] for r in result["rules"]]


def test_add_appends_and_before_inserts() -> None:
    result, dropped = resolve_edit(
        _baseline(),
        EditRequest(add=[RuleAdd(_SEND), RuleAdd(_SEND | {"path": "/first"}, before="apr_deny")]),
    )
    assert dropped == []
    assert result["target"] == TARGET_BINDING
    assert _paths(result) == ["/first", "/", "/repos", "/send"]
    assert [r["id"] for r in result["rules"]] == [None, "apr_deny", "apr_read", None]
    assert result["scopes"] == ["repo"]


def test_remove_drops_by_id_and_before_a_removed_rule_takes_its_place() -> None:
    result, _ = resolve_edit(
        _baseline(), EditRequest(remove=["apr_deny"], add=[RuleAdd(_SEND, before="apr_deny")])
    )
    assert _paths(result) == ["/send", "/repos"]


def test_set_replaces_the_list() -> None:
    result, _ = resolve_edit(_baseline(), EditRequest(set_rules=[_READ, _DENY]))
    assert _paths(result) == ["/repos", "/"]
    assert all(r["id"] is None for r in result["rules"])


@pytest.mark.parametrize(
    "request_",
    [EditRequest(remove=["apr_gone"]), EditRequest(add=[RuleAdd(_SEND, before="apr_gone")])],
    ids=["remove", "before"],
)
def test_unknown_rule_ids_are_refused(request_: EditRequest) -> None:
    with pytest.raises(UnknownRuleError):
        resolve_edit(_baseline(), request_)


def test_lenient_resolution_drops_unknown_parts() -> None:
    result, dropped = resolve_edit(
        _baseline(),
        EditRequest(remove=["apr_gone"], add=[RuleAdd(_SEND, before="apr_gone")]),
        lenient=True,
    )
    assert dropped == ["apr_gone", "apr_gone"]
    assert _paths(result) == ["/", "/repos", "/send"]


def test_resume_only_counts_on_a_suspended_binding() -> None:
    active, _ = resolve_edit(_baseline(), EditRequest(resume=True))
    with pytest.raises(NothingToChangeError):
        require_change(_baseline(), active)
    suspended = _baseline(suspended=True)
    resumed, _ = resolve_edit(suspended, EditRequest(resume=True))
    require_change(suspended, resumed)
    assert resumed["suspended"] is False
    assert widens_access(suspended, resumed) is True


def test_setting_the_same_rules_is_nothing_to_change() -> None:
    result, _ = resolve_edit(_baseline(), EditRequest(set_rules=[_DENY, _READ]))
    with pytest.raises(NothingToChangeError):
        require_change(_baseline(), result)


def test_widening() -> None:
    added, _ = resolve_edit(_baseline(), EditRequest(add=[RuleAdd(_SEND)]))
    assert widens_access(_baseline(), added) is True
    no_deny, _ = resolve_edit(_baseline(), EditRequest(remove=["apr_deny"]))
    assert widens_access(_baseline(), no_deny) is True
    no_read, _ = resolve_edit(_baseline(), EditRequest(remove=["apr_read"]))
    assert widens_access(_baseline(), no_read) is False
    reordered, _ = resolve_edit(_baseline(), EditRequest(set_rules=[_READ, _DENY]))
    assert widens_access(_baseline(), reordered) is True


def test_an_attached_rule_set_is_the_list_an_edit_changes() -> None:
    rule_set = {"id": "prs_1", "name": "ro", "curated": False, "rules": [_rule("prr_1", _READ)]}
    result, _ = resolve_edit(
        _baseline(rule_set=rule_set), EditRequest(add=[RuleAdd(_SEND, before="prr_1")])
    )
    assert result["target"] == TARGET_RULE_SET
    assert _paths(result) == ["/send", "/repos"]
    with pytest.raises(UnknownRuleError):
        resolve_edit(_baseline(rule_set=rule_set), EditRequest(remove=["apr_read"]))


def test_baseline_matches_ignores_scopes_but_not_rules_or_suspension() -> None:
    stored = _baseline()
    assert baseline_matches(stored, stored | {"granted_scopes": ["repo", "gist"]})
    assert not baseline_matches(stored, stored | {"suspended": True})
    assert not baseline_matches(stored, stored | {"rules": stored["rules"][:1]})


def test_adding_scopes_widens_and_counts_as_a_change() -> None:
    result, _ = resolve_edit(_baseline(), EditRequest(add_scopes=["gist"]))
    assert result["scopes"] == ["gist", "repo"]
    require_change(_baseline(), result)
    assert widens_access(_baseline(), result) is True
    already, _ = resolve_edit(_baseline(), EditRequest(add_scopes=["repo"]))
    with pytest.raises(NothingToChangeError):
        require_change(_baseline(), already)


def test_scopes_cannot_be_added_without_an_oauth_grant() -> None:
    with pytest.raises(ScopesNotSupportedError):
        resolve_edit(_baseline() | {"granted_scopes": None}, EditRequest(add_scopes=["x"]))


def test_removing_or_replacing_scopes_changes_the_grant_without_widening() -> None:
    base = _baseline() | {"granted_scopes": ["gist", "repo"]}
    removed, _ = resolve_edit(base, EditRequest(remove_scopes=["gist"]))
    assert removed["scopes"] == ["repo"]
    assert (removed_scopes(base, removed), scopes_changed(base, removed)) == (["gist"], True)
    require_change(base, removed)
    assert widens_access(base, removed) is False

    replaced, _ = resolve_edit(base, EditRequest(set_scopes=["repo", "read:org"]))
    assert replaced["scopes"] == ["read:org", "repo"]
    assert (added_scopes(base, replaced), removed_scopes(base, replaced)) == (
        ["read:org"],
        ["gist"],
    )
    assert widens_access(base, replaced) is True

    # Removing a scope the grant doesn't hold is already in place.
    absent, _ = resolve_edit(base, EditRequest(remove_scopes=["admin:org"]))
    assert scopes_changed(base, absent) is False
    with pytest.raises(NothingToChangeError):
        require_change(base, absent)


def test_scopes_cannot_be_removed_without_an_oauth_grant() -> None:
    with pytest.raises(ScopesNotSupportedError):
        resolve_edit(_baseline() | {"granted_scopes": None}, EditRequest(remove_scopes=["x"]))


def test_a_proposal_round_trips_its_scopes() -> None:
    request = EditRequest(add=[RuleAdd(_SEND)], add_scopes=["gist"], resume=True, reason="r")
    assert EditRequest.from_proposal(request.as_proposal()) == request
    for scopes in (EditRequest(remove_scopes=["gist"]), EditRequest(set_scopes=["repo"])):
        assert EditRequest.from_proposal(scopes.as_proposal()) == scopes
