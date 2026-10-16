"""SearchService ↔ strategy seam: config injection and query-time error mapping (#965).

Runs against a real in-memory SQLite registry session; the strategies are
test doubles registered under a dedicated mode so the built-in lexical
strategies stay untouched.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.repos.search import SearchUnsupportedError, register_strategy
from jentic_one.registry.repos.search.protocol import SearchCursor, SearchHit, SearchStrategy
from jentic_one.registry.repos.search.registry import _STRATEGIES
from jentic_one.registry.services.errors import SearchUnavailableError
from jentic_one.registry.services.search_service import SearchService
from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

_MODE = "test-seam"
_KEY = ("sqlite", _MODE)


class _QueryTimeFailingStrategy:
    """Configurable strategy whose provider is unavailable at query time."""

    dialect = "sqlite"
    name = _MODE
    #: The app config the last ``from_config`` call received.
    seen_app_config: AppConfig | None = None

    @classmethod
    def from_config(cls, config: Any, *, app_config: AppConfig | None = None) -> SearchStrategy:
        cls.seen_app_config = app_config
        return cls()

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
        raise SearchUnsupportedError("embedding provider unreachable")


@pytest.fixture()
def seam_strategy() -> Iterator[type[_QueryTimeFailingStrategy]]:
    _STRATEGIES.pop(_KEY, None)
    _QueryTimeFailingStrategy.seen_app_config = None
    register_strategy(_QueryTimeFailingStrategy)
    yield _QueryTimeFailingStrategy
    _STRATEGIES.pop(_KEY, None)


@pytest.fixture()
async def seam_ctx(sample_config_dict: dict[str, Any]) -> AsyncGenerator[Context, None]:
    """A Context-shaped object: real AppConfig + real in-memory SQLite registry DB."""
    app_config = AppConfig.model_validate(sample_config_dict)
    app_config.search.search_mode = _MODE
    registry_db = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
    await registry_db.connect()
    try:
        yield cast(Context, SimpleNamespace(config=app_config, registry_db=registry_db))
    finally:
        await registry_db.close()


@pytest.mark.asyncio
async def test_search_passes_app_config_to_strategy(
    seam_strategy: type[_QueryTimeFailingStrategy], seam_ctx: Context
) -> None:
    with pytest.raises(SearchUnavailableError):
        await SearchService(seam_ctx).search(query="anything")
    assert seam_strategy.seen_app_config is seam_ctx.config


@pytest.mark.asyncio
async def test_query_time_unsupported_maps_to_unavailable(
    seam_strategy: type[_QueryTimeFailingStrategy], seam_ctx: Context
) -> None:
    """A strategy failing inside ``search_operations`` surfaces as the service error."""
    with pytest.raises(SearchUnavailableError, match="embedding provider unreachable") as info:
        await SearchService(seam_ctx).search(query="anything")
    assert isinstance(info.value.__cause__, SearchUnsupportedError)
