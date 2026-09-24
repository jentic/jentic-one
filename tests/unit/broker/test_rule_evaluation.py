"""Unit tests for the rule-evaluation helpers in ``agent_rule_evaluator``.

These cover the pure functions (rule matching, ordered evaluation,
JSON-column coercion, fail-closed pattern compilation) that back the
direct-binding evaluator.
"""

from __future__ import annotations

from jentic_one.broker.repos.agent_rule_evaluator import (
    PermissionRule,
    _coerce_json_list,
    _compile_path,
    _normalize_methods,
    _rule_matches,
    evaluate_rules,
)
from jentic_one.shared.permissions.matching import compile_matcher

# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


def test_condition_less_rule_matches_everything() -> None:
    # `_rule_matches` itself is condition-agnostic — a condition-less rule still
    # structurally matches any request. The condition-less-`allow` *skip* is
    # enforced one layer up in `evaluate_rules`, not here.
    rule = PermissionRule(effect="allow", methods=None, path=None, operations=None)
    assert _rule_matches(rule, method="GET", path="/users", operation_id="getUsers")


def test_methods_match() -> None:
    rule = PermissionRule(
        effect="allow", methods=frozenset({"GET", "POST"}), path=None, operations=None
    )
    assert _rule_matches(rule, method="GET", path="/x", operation_id=None)
    assert _rule_matches(rule, method="post", path="/x", operation_id=None)
    assert not _rule_matches(rule, method="DELETE", path="/x", operation_id=None)


def test_path_regex_match_full_match_semantics() -> None:
    # #751: regex mode is full-match, so a bare ``/v1/users/.*`` matches
    # ``/v1/users/123`` but not ``/prefix/v1/users/123`` (which the old
    # start-anchored ``.match()`` behaviour would also have rejected — the
    # deliberate change is the tail: fullmatch is strict about what follows
    # the last capture, not about the prefix).
    rule = PermissionRule(
        effect="allow",
        methods=None,
        path=compile_matcher(r"/v1/users/.*", "regex"),
        operations=None,
    )
    assert _rule_matches(rule, method="GET", path="/v1/users/123", operation_id=None)
    assert not _rule_matches(rule, method="GET", path="/v2/users/123", operation_id=None)


def test_path_full_match_rejects_trailing_content() -> None:
    # Regression for #578-adjacent behaviour: full-match means the pattern
    # must describe the whole path — ``/v1/users/\d+`` does not match
    # ``/v1/users/42/roles``.
    rule = PermissionRule(
        effect="allow",
        methods=None,
        path=compile_matcher(r"/v1/users/\d+", "regex"),
        operations=None,
    )
    assert _rule_matches(rule, method="GET", path="/v1/users/42", operation_id=None)
    assert not _rule_matches(rule, method="GET", path="/v1/users/42/roles", operation_id=None)


def test_path_prefix_mode_is_literal() -> None:
    rule = PermissionRule(
        effect="allow",
        methods=None,
        path=compile_matcher("/v1/users", "prefix"),
        operations=None,
    )
    assert _rule_matches(rule, method="GET", path="/v1/users/42", operation_id=None)
    assert not _rule_matches(rule, method="GET", path="/v2/users", operation_id=None)


def test_path_exact_mode_is_literal() -> None:
    rule = PermissionRule(
        effect="allow",
        methods=None,
        path=compile_matcher("/v1/users", "exact"),
        operations=None,
    )
    assert _rule_matches(rule, method="GET", path="/v1/users", operation_id=None)
    assert not _rule_matches(rule, method="GET", path="/v1/users/42", operation_id=None)


def test_operations_match() -> None:
    rule = PermissionRule(
        effect="allow", methods=None, path=None, operations=("getUser", "listUsers")
    )
    assert _rule_matches(rule, method="GET", path="/x", operation_id="getUser")
    assert not _rule_matches(rule, method="GET", path="/x", operation_id="deleteUser")


def test_operations_no_match_when_operation_id_is_none() -> None:
    rule = PermissionRule(effect="allow", methods=None, path=None, operations=("getUser",))
    assert not _rule_matches(rule, method="GET", path="/x", operation_id=None)


def test_all_criteria_must_match() -> None:
    rule = PermissionRule(
        effect="allow",
        methods=frozenset({"GET"}),
        path=compile_matcher(r"/api/.*", "regex"),
        operations=("getUser",),
    )
    assert _rule_matches(rule, method="GET", path="/api/users", operation_id="getUser")
    assert not _rule_matches(rule, method="POST", path="/api/users", operation_id="getUser")
    assert not _rule_matches(rule, method="GET", path="/other", operation_id="getUser")
    assert not _rule_matches(rule, method="GET", path="/api/users", operation_id="listUsers")


# ---------------------------------------------------------------------------
# Pattern compilation safety (delegates to shared seam; logs on fail-closed)
# ---------------------------------------------------------------------------


def test_compile_none_returns_none() -> None:
    assert _compile_path(None, "regex", binding="agnt_1:cred_1") is None


def test_compile_valid_returns_matcher() -> None:
    m = _compile_path(r"/v1/.*", "regex", binding="agnt_1:cred_1")
    assert m is not None
    assert m.never is False
    assert m.matches("/v1/foo") is True


def test_compile_invalid_regex_is_fail_closed() -> None:
    m = _compile_path("[invalid", "regex", binding="agnt_1:cred_1")
    assert m is not None
    assert m.never is True
    # Fail-closed replaces the pre-#751 silent wildcard: an unparseable
    # legacy row now blocks every request rather than accidentally granting
    # everything.
    assert m.matches("/anything") is False


def test_compile_oversized_is_fail_closed() -> None:
    m = _compile_path("a" * 1001, "regex", binding="agnt_1:cred_1")
    assert m is not None
    assert m.never is True


# ---------------------------------------------------------------------------
# Method normalization
# ---------------------------------------------------------------------------


def test_normalize_methods_none() -> None:
    assert _normalize_methods(None) is None


def test_normalize_methods_uppercases() -> None:
    result = _normalize_methods(["get", "Post", "DELETE"])
    assert result == frozenset({"GET", "POST", "DELETE"})


# ---------------------------------------------------------------------------
# JSON-column coercion (raw-SQL read path — SQLite returns JSON as TEXT)
# ---------------------------------------------------------------------------


def test_coerce_json_list_none() -> None:
    assert _coerce_json_list(None) is None


def test_coerce_json_list_passes_through_decoded_list() -> None:
    """Postgres JSONB is already decoded to a list — returned unchanged."""
    assert _coerce_json_list(["GET", "POST"]) == ["GET", "POST"]


def test_coerce_json_list_parses_sqlite_json_string() -> None:
    """SQLite returns the column as a raw JSON string via ``text()`` SQL."""
    assert _coerce_json_list('["GET", "POST", "PUT"]') == ["GET", "POST", "PUT"]


def test_coerce_json_list_json_null_string_is_none() -> None:
    """A SQLite ``operations='null'`` column must decode to None, not ['n','u','l','l']."""
    assert _coerce_json_list("null") is None


def test_coerce_json_list_invalid_json_is_none() -> None:
    assert _coerce_json_list("not-json") is None


# ---------------------------------------------------------------------------
# Ordered rule-list evaluation
# ---------------------------------------------------------------------------


def test_empty_rules_denies() -> None:
    assert evaluate_rules([], method="GET", path="/x", operation_id=None) is False


def test_no_rules_means_all_operations_denied() -> None:
    """A binding with no permission rules denies every request.

    This is the secure-by-default posture — users must explicitly add allow
    rules. No implicit system rules are auto-created, so an empty rule list
    results in unconditional denial regardless of method, path, or operation.
    """
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert evaluate_rules([], method=method, path="/any/path", operation_id="anyOp") is False


def test_first_match_wins_allow() -> None:
    rules = [
        PermissionRule(effect="allow", methods=frozenset({"GET"}), path=None, operations=None),
        PermissionRule(effect="deny", methods=None, path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="GET", path="/x", operation_id=None) is True


def test_first_match_wins_deny() -> None:
    rules = [
        PermissionRule(effect="deny", methods=frozenset({"DELETE"}), path=None, operations=None),
        PermissionRule(effect="allow", methods=None, path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="DELETE", path="/x", operation_id=None) is False


def test_skips_non_matching_rules() -> None:
    rules = [
        PermissionRule(effect="deny", methods=frozenset({"DELETE"}), path=None, operations=None),
        PermissionRule(effect="allow", methods=frozenset({"GET"}), path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="GET", path="/x", operation_id=None) is True


def test_no_match_defaults_to_deny() -> None:
    rules = [
        PermissionRule(effect="allow", methods=frozenset({"POST"}), path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="GET", path="/x", operation_id=None) is False


def test_condition_less_allow_is_ignored() -> None:
    """A condition-less `allow` is a misconfiguration — skipped, not match-all.

    It should have been rejected at the API schema (422); if one slips through to
    the broker it must NOT grant blanket access. With only that rule, the list is
    effectively empty and the request falls through to default-deny.
    """
    rules = [
        PermissionRule(effect="allow", methods=None, path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="PUT", path="/anything", operation_id="op") is False


def test_condition_less_deny_still_matches_all() -> None:
    """A condition-less `deny` keeps its legitimate match-all (catch-all) behaviour."""
    rules = [
        PermissionRule(effect="deny", methods=None, path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="GET", path="/anything", operation_id="op") is False


def test_constrained_allow_reached_after_skipped_condition_less_allow() -> None:
    """A skipped condition-less `allow` must not short-circuit later constrained rules."""
    rules = [
        PermissionRule(effect="allow", methods=None, path=None, operations=None),
        PermissionRule(effect="allow", methods=frozenset({"GET"}), path=None, operations=None),
    ]
    assert evaluate_rules(rules, method="GET", path="/x", operation_id=None) is True
    # A method the constrained allow doesn't cover still falls through to deny.
    assert evaluate_rules(rules, method="POST", path="/x", operation_id=None) is False


def test_deny_specific_then_constrained_allow_all_methods() -> None:
    rules = [
        PermissionRule(
            effect="deny",
            methods=frozenset({"DELETE"}),
            path=None,
            operations=("deleteUser",),
        ),
        PermissionRule(
            effect="allow",
            methods=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"}),
            path=None,
            operations=None,
        ),
    ]
    assert evaluate_rules(rules, method="DELETE", path="/u", operation_id="deleteUser") is False
    assert evaluate_rules(rules, method="GET", path="/u", operation_id="getUser") is True
