"""Unit tests for the direct-binding permission-rule evaluator (theme-5 Phase 2).

Rule *matching* semantics are shared with the toolkit evaluator (covered in
``test_rule_evaluator.py``); these tests cover what is new here: the
``(agent, credential)`` keying, the rule-set indirection (which query runs and
how results are cached), and the deny outcomes the router keys its problem
detail on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.repos.agent_rule_evaluator import AgentRuleEvaluator
from jentic_one.shared.broker.protocols import AgentRuleEvaluatorProtocol


class _AsyncCtx:
    """Helper to simulate an async context manager for session."""

    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        pass


def _mock_db(rows: list[tuple[object, ...]]) -> tuple[MagicMock, AsyncMock]:
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = rows
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))
    return mock_db, mock_session


def test_satisfies_protocol() -> None:
    assert issubclass(AgentRuleEvaluator, AgentRuleEvaluatorProtocol)


@pytest.mark.asyncio
async def test_empty_rules_denies_with_zero_loaded() -> None:
    """A binding with no rules defaults to deny; rules_loaded=0 keys the detail."""
    mock_db, _ = _mock_db([])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="GET",
        path="/x",
        operation_id=None,
    )
    assert result.allowed is False
    assert result.rules_loaded == 0


@pytest.mark.asyncio
async def test_matching_allow_rule_allows() -> None:
    mock_db, _ = _mock_db([("allow", '["GET"]', ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="GET",
        path="/v1/things",
        operation_id=None,
    )
    assert result.allowed is True
    assert result.rules_loaded == 1


@pytest.mark.asyncio
async def test_loaded_but_unmatched_denies_with_count() -> None:
    """Rules loaded but nothing matched → deny with rules_loaded > 0 (#578 twin)."""
    mock_db, _ = _mock_db([("allow", '["GET"]', ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="DELETE",
        path="/v1/things",
        operation_id=None,
    )
    assert result.allowed is False
    assert result.rules_loaded == 1


@pytest.mark.asyncio
async def test_inline_rules_query_keyed_on_binding() -> None:
    """Without a rule set, the binding query runs with agent+credential params."""
    mock_db, mock_session = _mock_db([("allow", None, ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="GET",
        path="/x",
        operation_id=None,
    )
    params = mock_session.execute.call_args.args[1]
    assert params == {"agent_id": "agt_1", "credential_id": "cred_1"}


@pytest.mark.asyncio
async def test_rule_set_query_replaces_inline_rules() -> None:
    """A binding with a rule_set_id evaluates the shared set, not the inline rows."""
    mock_db, mock_session = _mock_db([("allow", None, ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id="prs_shared",
        method="GET",
        path="/x",
        operation_id=None,
    )
    assert result.allowed is True
    params = mock_session.execute.call_args.args[1]
    assert params == {"rule_set_id": "prs_shared"}


@pytest.mark.asyncio
async def test_cached_second_call_same_binding() -> None:
    """Second evaluate for the same binding is served from cache."""
    mock_db, mock_session = _mock_db([("allow", None, ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    for method in ("GET", "POST"):
        await evaluator.evaluate(
            agent_id="agt_1",
            credential_id="cred_1",
            rule_set_id=None,
            method=method,
            path="/x",
            operation_id=None,
        )
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_distinct_bindings_cached_separately() -> None:
    """Different (agent, credential) bindings never share a cache entry."""
    mock_db, mock_session = _mock_db([("allow", None, ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    for cred in ("cred_1", "cred_2"):
        await evaluator.evaluate(
            agent_id="agt_1",
            credential_id=cred,
            rule_set_id=None,
            method="GET",
            path="/x",
            operation_id=None,
        )
    assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_rule_set_cached_across_bindings() -> None:
    """A shared rule set attached to N bindings is fetched once, not N times."""
    mock_db, mock_session = _mock_db([("allow", None, ".*", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    for agent, cred in (("agt_1", "cred_1"), ("agt_2", "cred_2")):
        await evaluator.evaluate(
            agent_id=agent,
            credential_id=cred,
            rule_set_id="prs_shared",
            method="GET",
            path="/x",
            operation_id=None,
        )
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_condition_less_allow_skipped() -> None:
    """Defense-in-depth carries over: a condition-less allow is never honoured."""
    mock_db, _ = _mock_db([("allow", None, None, None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="GET",
        path="/x",
        operation_id=None,
    )
    assert result.allowed is False
    assert result.rules_loaded == 1


@pytest.mark.asyncio
async def test_invalid_stored_path_is_fail_closed() -> None:
    """An unparseable stored pattern never matches (#751 carries over)."""
    mock_db, _ = _mock_db([("allow", None, "([unclosed", None, "regex")])
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="GET",
        path="([unclosed",
        operation_id=None,
    )
    assert result.allowed is False
    assert result.rules_loaded == 1


@pytest.mark.asyncio
async def test_coerces_sqlite_json_string_methods() -> None:
    """Regression: SQLite returns ``methods``/``operations`` as raw JSON strings.

    The evaluator reads rules via raw ``text()`` SQL, bypassing the ORM's JSON
    deserialization. On SQLite ``methods`` arrives as ``'["GET", ...]'`` and
    ``operations`` as ``'null'``; without coercion these get iterated
    character-by-character, so a legitimate ``allow`` rule silently fails to
    match. Feed the SQLite wire form and assert the method still matches.
    """
    mock_db, _ = _mock_db(
        [("allow", '["GET", "POST", "PUT", "PATCH", "DELETE"]', ".*", "null", "regex")]
    )
    evaluator = AgentRuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    allowed = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="POST",
        path="/v1/things",
        operation_id=None,
    )
    assert allowed.allowed is True
    assert allowed.rules_loaded == 1

    # A method outside the (correctly parsed) set must NOT match.
    denied = await evaluator.evaluate(
        agent_id="agt_1",
        credential_id="cred_1",
        rule_set_id=None,
        method="OPTIONS",
        path="/v1/things",
        operation_id=None,
    )
    assert denied.allowed is False
    # Non-zero rules_loaded distinguishes "loaded but no match" from "no rules".
    assert denied.rules_loaded == 1
