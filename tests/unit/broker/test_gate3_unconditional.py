"""Regression tests proving Gate 3 (permission rules) executes unconditionally.

The rule evaluator must be invoked for every request that passes Gate 2
(toolkit derivation). Even if the toolkit_id were empty — or the binding has
zero rules — the evaluator returns False and the request is denied. This is the
secure-by-default contract (issue #576).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.repos.rule_evaluator import RuleEvaluator, evaluate_rules


class _AsyncCtx:
    """Simulate an async context manager for the DB session."""

    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        pass


def test_evaluate_rules_empty_list_denies() -> None:
    """Zero rules → evaluator returns False → request denied."""
    assert evaluate_rules([], method="GET", path="/any", operation_id="op") is False


@pytest.mark.asyncio
async def test_evaluator_denies_when_no_rules_loaded() -> None:
    """Full RuleEvaluator path: empty DB → deny verdict."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        toolkit_id="tk_test",
        method="POST",
        path="/v1/resource",
        operation_id="createResource",
        api_vendor="acme",
    )
    assert result.allowed is False
    assert result.rules_loaded == 0


@pytest.mark.asyncio
async def test_evaluator_denies_with_empty_toolkit_id() -> None:
    """Even if toolkit_id were empty, evaluator runs and denies."""
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.session = MagicMock(return_value=_AsyncCtx(mock_session))

    evaluator = RuleEvaluator(mock_db, cache_ttl_seconds=300.0)
    result = await evaluator.evaluate(
        toolkit_id="",
        method="GET",
        path="/anything",
        operation_id=None,
        api_vendor="vendor",
    )
    assert result.allowed is False
