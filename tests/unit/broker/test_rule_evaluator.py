"""Unit tests for the toolkit permission-rule evaluator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.repos.rule_evaluator import (
    PermissionRule,
    RuleEvaluator,
    _coerce_json_list,
    _compile_path_for_rule,
    _normalize_methods,
    _rule_matches,
    evaluate_rules,
)
from jentic_one.shared.broker.protocols import RuleEvaluatorProtocol
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
    # must describe the whole path — ``/v1/users/\d+`` no longer matches
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
    assert _compile_path_for_rule(None, "regex", toolkit_id="tk_1") is None


def test_compile_valid_returns_matcher() -> None:
    m = _compile_path_for_rule(r"/v1/.*", "regex", toolkit_id="tk_1")
    assert m is not None
    assert m.never is False
    assert m.matches("/v1/foo") is True


def test_compile_invalid_regex_is_fail_closed() -> None:
    m = _compile_path_for_rule("[invalid", "regex", toolkit_id="tk_1")
    assert m is not None
    assert m.never is True
    # Fail-closed replaces the pre-#751 silent wildcard: an unparseable
    # legacy row now blocks every request rather than accidentally granting
    # everything.
    assert m.matches("/anything") is False


def test_compile_oversized_is_fail_closed() -> None:
    m = _compile_path_for_rule("a" * 1001, "regex", toolkit_id="tk_1")
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


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_satisfies_protocol() -> None:
    assert issubclass(RuleEvaluator, RuleEvaluatorProtocol)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class _AsyncCtx:
    """Helper to simulate an async context manager for session."""

    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        pass


@pytest.mark.asyncio
async def test_evaluator_empty_rules_denies() -> None:
    """A toolkit with no rules defaults to deny."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    assert result.allowed is False
    # #578: rules_loaded is 0 when nothing was loaded for the pool — the
    # router keys its two-variant deny detail on this.
    assert result.rules_loaded == 0


@pytest.mark.asyncio
async def test_evaluator_coerces_sqlite_json_string_methods() -> None:
    """Regression: SQLite returns ``methods``/``operations`` as raw JSON strings.

    The evaluator reads rules via raw ``text()`` SQL, bypassing the ORM's JSON
    deserialization. On SQLite ``methods`` arrives as ``'["GET", ...]'`` and
    ``operations`` as ``'null'``; without coercion these get iterated
    character-by-character, so a legitimate ``allow`` rule silently fails to
    match. Feed the SQLite wire form and assert the method still matches.
    """
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("allow", '["GET", "POST", "PUT", "PATCH", "DELETE"]', ".*", "null", "regex")
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    allowed = await evaluator.evaluate(
        toolkit_id="tk_1", method="POST", path="/v1/things", operation_id=None, api_vendor="acme"
    )
    assert allowed.allowed is True
    assert allowed.rules_loaded == 1

    # A method outside the (correctly parsed) set must NOT match.
    denied = await evaluator.evaluate(
        toolkit_id="tk_1", method="OPTIONS", path="/v1/things", operation_id=None, api_vendor="acme"
    )
    assert denied.allowed is False
    # Non-zero rules_loaded distinguishes "loaded but no match" from "no rules".
    assert denied.rules_loaded == 1


@pytest.mark.asyncio
async def test_evaluator_cached_second_call() -> None:
    """Second evaluate call for same toolkit+vendor uses cache."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("allow", None, ".*", None, "regex")]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    r1 = await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    r2 = await evaluator.evaluate(
        toolkit_id="tk_1", method="POST", path="/y", operation_id="op", api_vendor="acme"
    )
    assert r1.allowed is True
    assert r2.allowed is True
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_evaluator_distinct_toolkits_separate_cache() -> None:
    """Different toolkit IDs are cached independently."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("allow", None, ".*", None, "regex")]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    await evaluator.evaluate(
        toolkit_id="tk_2", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_evaluator_distinct_vendors_separate_cache() -> None:
    """Different api_vendor values are cached independently."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("allow", None, ".*", None, "regex")]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="other"
    )
    assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_evaluator_clear_drops_cache() -> None:
    """clear() forces a re-fetch on the next call."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("allow", None, ".*", None, "regex")]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    evaluator.clear()
    await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    assert mock_session.execute.call_count == 2
