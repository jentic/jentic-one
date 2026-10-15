"""Search protocol and result types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.config import AppConfig, SearchConfig


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single search match for an operation."""

    operation_id: str
    revision_id: uuid.UUID
    api_id: uuid.UUID
    distance: float
    score: float | None = None


@dataclass(frozen=True, slots=True)
class SearchCursor:
    """Keyset cursor encoding (distance, operation_id) for tie-breaking."""

    distance: float
    operation_id: str


@runtime_checkable
class SearchStrategy(Protocol):
    """Strategy protocol for pluggable search modes.

    Each strategy declares its mode name and target dialect.

    Construction: :func:`resolve_strategy` builds a strategy zero-arg
    (``cls()``) unless the class declares the optional
    :class:`ConfigurableSearchStrategy` hook — a ``from_config`` classmethod —
    in which case it calls ``cls.from_config(config, app_config=...)`` with the
    active :class:`SearchConfig` and the application config the server booted
    with. Use the hook for strategies that need configuration (e.g. an
    embedding provider read from a registered config section) instead of
    loading config on the request path.

    Errors: raise
    :class:`~jentic_one.registry.repos.search.errors.SearchUnsupportedError`
    from ``from_config`` or ``search_operations`` when the strategy cannot
    serve the search (missing dependency, provider down, misconfiguration).
    The service layer maps it to its unavailable-search error (HTTP 501
    ``search_unsupported``); never raise service-layer errors from a strategy.
    """

    name: str
    dialect: str

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
        """Return operations ordered by ascending distance/dissimilarity."""
        ...


@runtime_checkable
class ConfigurableSearchStrategy(Protocol):
    """Optional construction hook for strategies that need configuration.

    A :class:`SearchStrategy` class that also defines ``from_config`` is built
    through it by :func:`resolve_strategy`; classes without it are built
    zero-arg. ``resolve_strategy`` runs once per search request, so keep the
    hook cheap — cache expensive clients (e.g. an embedder) at module or class
    level keyed on the config that built them.
    """

    @classmethod
    def from_config(
        cls,
        config: SearchConfig,
        *,
        app_config: AppConfig | None = None,
    ) -> SearchStrategy:
        """Build the strategy from the active search and application config.

        *app_config* is the config the server booted with (reach registered
        extension sections via ``app_config.extension(name)``). It is ``None``
        only when the caller resolves with a bare :class:`SearchConfig`;
        raise ``SearchUnsupportedError`` if the strategy cannot run without it.
        """
        ...
