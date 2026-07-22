"""Unit tests for the toolkit permission-rule evaluator."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.repos.rule_evaluator import (
    PermissionRule,
    RuleEvaluator,
    _coerce_json_list,
    _compile_path,
    _normalize_methods,
    _rule_matches,
    evaluate_rules,
)
from jentic_one.shared.broker.protocols import RuleEvaluatorProtocol

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


def test_path_regex_match() -> None:
    rule = PermissionRule(
        effect="allow", methods=None, path=re.compile(r"^/v1/users/.*"), operations=None
    )
    assert _rule_matches(rule, method="GET", path="/v1/users/123", operation_id=None)
    assert not _rule_matches(rule, method="GET", path="/v2/users/123", operation_id=None)


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
        path=re.compile(r"^/api/.*"),
        operations=("getUser",),
    )
    assert _rule_matches(rule, method="GET", path="/api/users", operation_id="getUser")
    assert not _rule_matches(rule, method="POST", path="/api/users", operation_id="getUser")
    assert not _rule_matches(rule, method="GET", path="/other", operation_id="getUser")
    assert not _rule_matches(rule, method="GET", path="/api/users", operation_id="listUsers")


# ---------------------------------------------------------------------------
# Pattern compilation safety
# ---------------------------------------------------------------------------


def test_compile_path_none() -> None:
    assert _compile_path(None) is None


def test_compile_path_valid() -> None:
    pat = _compile_path(r"^/v1/.*")
    assert pat is not None
    assert pat.match("/v1/foo")


def test_compile_path_invalid_regex() -> None:
    assert _compile_path("[invalid") is None


def test_compile_path_oversized() -> None:
    assert _compile_path("a" * 1001) is None


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
    assert result is False


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
        ("allow", '["GET", "POST", "PUT", "PATCH", "DELETE"]', ".*", "null")
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    allowed = await evaluator.evaluate(
        toolkit_id="tk_1", method="POST", path="/v1/things", operation_id=None, api_vendor="acme"
    )
    assert allowed is True

    # A method outside the (correctly parsed) set must NOT match.
    denied = await evaluator.evaluate(
        toolkit_id="tk_1", method="OPTIONS", path="/v1/things", operation_id=None, api_vendor="acme"
    )
    assert denied is False


@pytest.mark.asyncio
async def test_evaluator_cached_second_call() -> None:
    """Second evaluate call for same toolkit+vendor uses cache."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("allow", None, ".*", None)]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    r1 = await evaluator.evaluate(
        toolkit_id="tk_1", method="GET", path="/x", operation_id=None, api_vendor="acme"
    )
    r2 = await evaluator.evaluate(
        toolkit_id="tk_1", method="POST", path="/y", operation_id="op", api_vendor="acme"
    )
    assert r1 is True
    assert r2 is True
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_evaluator_distinct_toolkits_separate_cache() -> None:
    """Different toolkit IDs are cached independently."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("allow", None, ".*", None)]
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
    mock_result.all.return_value = [("allow", None, ".*", None)]
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
    mock_result.all.return_value = [("allow", None, ".*", None)]
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
