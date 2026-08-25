"""Tests for the search strategy registry."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.repos.search import (
    SearchUnsupportedError,
    resolve_strategy,
)
from jentic_one.registry.repos.search.postgres_lexical import PostgresLexicalStrategy
from jentic_one.registry.repos.search.protocol import SearchCursor, SearchHit
from jentic_one.registry.repos.search.registry import _STRATEGIES, register_strategy
from jentic_one.registry.repos.search.sqlite_lexical import SqliteLexicalStrategy
from jentic_one.shared.config import DatabaseConfig, SearchConfig
from jentic_one.shared.db.backends import get_backend

_TEST_KEY = ("test-dialect", "test-mode")


class _FakeStrategy:
    dialect = _TEST_KEY[0]
    name = _TEST_KEY[1]

    async def search_operations(
        self,
        session: AsyncSession,
        *,
        query: str,
        api_filters: list[uuid.UUID] | None = None,
        revision_pins: dict[uuid.UUID, uuid.UUID] | None = None,
        limit: int = 20,
        cursor: SearchCursor | None = None,
    ) -> list[SearchHit]:
        return []


class _OtherFakeStrategy(_FakeStrategy):
    """A different class claiming the same (dialect, name) key."""


@pytest.fixture()
def clean_test_key() -> Iterator[None]:
    """Keep the process-global registry free of the test key."""
    _STRATEGIES.pop(_TEST_KEY, None)
    yield
    _STRATEGIES.pop(_TEST_KEY, None)


def test_postgres_lexical_resolves() -> None:
    backend = get_backend(DatabaseConfig(name="reg"))
    config = SearchConfig(search_mode="lexical")
    strategy = resolve_strategy(backend, config)
    assert isinstance(strategy, PostgresLexicalStrategy)
    assert strategy.name == "lexical"


def test_sqlite_lexical_resolves() -> None:
    backend = get_backend(DatabaseConfig(backend="sqlite", path=":memory:"))
    config = SearchConfig(search_mode="lexical")
    strategy = resolve_strategy(backend, config)
    assert isinstance(strategy, SqliteLexicalStrategy)
    assert strategy.name == "lexical"


def test_unknown_mode_raises() -> None:
    backend = get_backend(DatabaseConfig(name="reg"))
    config = SearchConfig(search_mode="lexical")
    # Hack the mode to test unknown lookup.
    object.__setattr__(config, "search_mode", "unknown_mode")
    with pytest.raises(SearchUnsupportedError, match="No search strategy"):
        resolve_strategy(backend, config)


def test_register_identical_class_is_idempotent(clean_test_key: None) -> None:
    assert register_strategy(_FakeStrategy) is _FakeStrategy
    # Double import re-runs the decorator with the very same class — a no-op.
    assert register_strategy(_FakeStrategy) is _FakeStrategy
    assert _STRATEGIES[_TEST_KEY] is _FakeStrategy


def test_register_conflicting_class_raises(clean_test_key: None) -> None:
    register_strategy(_FakeStrategy)
    with pytest.raises(ValueError, match="refusing to shadow"):
        register_strategy(_OtherFakeStrategy)
    # The original registration must survive the rejected shadowing attempt.
    assert _STRATEGIES[_TEST_KEY] is _FakeStrategy


def test_register_does_not_shadow_builtin_lexical() -> None:
    """An extension can't silently replace the OSS lexical strategy (#958)."""

    class _ShadowLexical(_FakeStrategy):
        dialect = PostgresLexicalStrategy.dialect
        name = PostgresLexicalStrategy.name

    builtin_key = (PostgresLexicalStrategy.dialect, PostgresLexicalStrategy.name)
    # Snapshot/restore the built-in entry so a future regression (guard allows the
    # write) can't corrupt the process-global registry for later tests.
    original = _STRATEGIES.get(builtin_key)
    try:
        with pytest.raises(ValueError, match="refusing to shadow"):
            register_strategy(_ShadowLexical)
        assert _STRATEGIES[builtin_key] is PostgresLexicalStrategy
    finally:
        if original is not None:
            _STRATEGIES[builtin_key] = original
