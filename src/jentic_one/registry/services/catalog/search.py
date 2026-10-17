"""Extension seam: pluggable ranking for ``GET /catalog?q=``.

A *catalog search strategy* turns a query plus the already-filtered manifest
entries into a ranked candidate list. ``CatalogService.list_all`` owns
everything around that call — browse-vs-search routing, filtering, validation,
ordering, keyset paging, cursors and fallback — so a strategy only ranks.

OSS ships one strategy, :class:`TokenSearchStrategy` (``name = "token"``), the
built-in token-overlap scorer. It is registered at import time and is the
default ``catalog.search_strategy``; with nothing else registered, responses
and cursors are exactly the token scorer's.

Contract for a strategy:

- ``rank()`` is only called for a **search**: ``q`` has at least one token
  under the catalog tokeniser (see :func:`manifest_builder.has_query_tokens`).
  Empty or token-free queries are served on the browse path and never reach a
  strategy.
- ``entries`` is already narrowed by the list filters. Return the **full**
  ranked candidate list — the service pages it, so never apply a limit.
- The service, not the strategy, owns the final order and validity: it drops
  rows whose score is not a finite ``int``/``float``, rows whose entry is not
  in ``entries`` (matched by ``api_id``; the service's own entry is served),
  and duplicate ``api_id`` rows (the best-scoring one is kept), then sorts by
  ``(-score, api_id)``. Higher scores rank first.
- Ranking must be deterministic for a fixed ``(q, entries)``: a walk re-ranks
  on every page and resumes after the cursor's ``(score, api_id)`` position.
- Raise to signal failure. On the first page of a walk the service falls back
  to ``token`` for the whole walk; on a later page it answers 503 (the walk's
  order can't be continued by a different ranker).
- ``deadline_ms`` is the service-enforced budget for the call (the service
  cancels an awaiting ``rank()`` once it passes, which counts as a failure).
  Keep CPU-bound work short or offload it: cancellation can only interrupt an
  ``await``.
- ``context`` is reserved for request-scoped hints; ``CatalogService`` passes
  ``None``. Strategies must tolerate ``None`` and ignore unknown keys.
- ``RankedEntry.match`` is optional evidence returned to clients as each
  item's ``match``. Leave it ``None`` to omit it.

Registration mirrors the other process-global seams
(``registry/repos/search/registry.py``, ``registry/ingest/pipeline/stage_registry.py``):
call :func:`register_catalog_search_strategy` at import time, before the app
is assembled; select it with ``catalog.search_strategy``. App assembly fails
with a ``ConfigError`` when the configured name is not registered.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.shared.config import AppConfig, ConfigError

__all__ = [
    "TOKEN_STRATEGY_NAME",
    "CatalogMatch",
    "CatalogSearchStrategy",
    "NormalizedRanking",
    "RankedEntry",
    "TokenSearchStrategy",
    "UnknownCatalogSearchStrategyError",
    "normalize_ranking",
    "register_catalog_search_strategy",
    "registered_catalog_search_strategies",
    "resolve_catalog_search_strategy",
    "validate_catalog_search_config",
]

#: Name of the built-in default strategy. Cursors from this strategy carry no
#: strategy key, so their bytes are the token scorer's.
TOKEN_STRATEGY_NAME = "token"

#: Strategy names are written into cursors, so keep them short and plain.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class CatalogMatch:
    """Why an entry matched. Optional, returned to clients as ``match``.

    ``channels`` names the kinds of evidence behind the match (e.g.
    ``("token",)``). It is an open set: clients must accept values they don't
    know.
    """

    channels: tuple[str, ...]


@dataclass(frozen=True)
class RankedEntry:
    """One ranked candidate: higher ``score`` first, ties broken by ``api_id``."""

    entry: mb.ManifestEntry
    score: int | float
    match: CatalogMatch | None = None


@runtime_checkable
class CatalogSearchStrategy(Protocol):
    """Ranks filtered catalog entries for a search query (see the module contract)."""

    #: Stable registry key, recorded in cursors of walks this strategy served.
    name: str

    async def rank(
        self,
        q: str,
        entries: Sequence[mb.ManifestEntry],
        *,
        deadline_ms: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> list[RankedEntry]:
        """Return the full ranked candidate list (no limit). Raise to fall back."""
        ...


class TokenSearchStrategy:
    """Default strategy: the built-in token-overlap scorer, integer scores.

    Scores are passed through from ``manifest_builder.score_entries`` untouched
    (``int``), so cursors keep their integer ``s`` and existing bytes.
    """

    name = TOKEN_STRATEGY_NAME

    async def rank(
        self,
        q: str,
        entries: Sequence[mb.ManifestEntry],
        *,
        deadline_ms: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> list[RankedEntry]:
        """Rank ``entries`` by token overlap with ``q``; non-matches are omitted."""
        return [
            RankedEntry(entry=entry, score=score)
            for entry, score in mb.score_entries(list(entries), q)
            if score is not None
        ]


class UnknownCatalogSearchStrategyError(LookupError):
    """Raised when no catalog search strategy is registered under a name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No catalog search strategy registered as {name!r}")
        self.name = name


_STRATEGIES: dict[str, CatalogSearchStrategy] = {TOKEN_STRATEGY_NAME: TokenSearchStrategy()}


def register_catalog_search_strategy(strategy: CatalogSearchStrategy) -> None:
    """Register a catalog search strategy under ``strategy.name``.

    Call at import time, before the app is assembled. Idempotent for the same
    (or an equal) strategy object, so a double import is safe; raises
    ``ValueError`` when the name is already bound to a different strategy — an
    extension can't silently shadow ``token`` or another extension's strategy.
    Same posture as ``register_strategy`` and ``register_pipeline_stage``.
    """
    name = getattr(strategy, "name", None)
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        msg = (
            f"Catalog search strategy name {name!r} is invalid "
            "(expected lowercase letters, digits and '_', starting with a letter, max 32)"
        )
        raise ValueError(msg)
    if not isinstance(strategy, CatalogSearchStrategy):
        msg = f"Catalog search strategy {name!r} does not implement rank()"
        raise TypeError(msg)
    existing = _STRATEGIES.get(name)
    if existing is not None and existing is not strategy and existing != strategy:
        msg = (
            f"Catalog search strategy {name!r} is already registered by "
            f"{type(existing).__module__}.{type(existing).__qualname__}; refusing to "
            f"shadow it with {type(strategy).__module__}.{type(strategy).__qualname__}"
        )
        raise ValueError(msg)
    if existing is None:
        _STRATEGIES[name] = strategy


def registered_catalog_search_strategies() -> Mapping[str, CatalogSearchStrategy]:
    """Snapshot of the registered strategies, keyed by name (registration order)."""
    return dict(_STRATEGIES)


def resolve_catalog_search_strategy(name: str) -> CatalogSearchStrategy:
    """Return the strategy registered as ``name``.

    Raises :class:`UnknownCatalogSearchStrategyError` when nothing is registered
    under that name.
    """
    strategy = _STRATEGIES.get(name)
    if strategy is None:
        raise UnknownCatalogSearchStrategyError(name)
    return strategy


def validate_catalog_search_config(config: AppConfig) -> None:
    """Fail app assembly when ``catalog.search_strategy`` names no registered strategy.

    Raises ``ConfigError`` naming the registered strategies, so a typo or a
    missing extension is a boot error rather than a silent switch to ``token``.
    """
    name = config.catalog.search_strategy
    if name not in _STRATEGIES:
        msg = (
            f"catalog.search_strategy {name!r} is not a registered catalog search "
            f"strategy (registered: {sorted(_STRATEGIES)})"
        )
        raise ConfigError(msg)


#: Bounds on client-facing ``match`` evidence a strategy may attach.
_MAX_MATCH_CHANNELS = 8
_MAX_CHANNEL_LEN = 32


@dataclass(frozen=True)
class NormalizedRanking:
    """A strategy's ranking after the service's validation (see :func:`normalize_ranking`)."""

    #: Valid rows, unique by ``api_id``, sorted by ``(-score, api_id)``; each
    #: row's ``entry`` is the service's own entry for that ``api_id``.
    rows: list[RankedEntry]
    #: Rows whose score is not a finite ``int``/``float`` (or not a ``RankedEntry``).
    dropped_invalid: int = 0
    #: Rows whose ``api_id`` is not among the filtered entries.
    dropped_foreign: int = 0
    #: Lower-scoring repeats of an ``api_id`` already kept.
    dropped_duplicate: int = 0


def _valid_score(score: object) -> bool:
    if isinstance(score, bool) or not isinstance(score, int | float):
        return False
    return math.isfinite(score)


def _clean_match(match: object) -> CatalogMatch | None:
    """Keep well-formed, bounded ``match`` evidence; drop anything else."""
    if not isinstance(match, CatalogMatch) or not isinstance(match.channels, tuple):
        return None
    channels = tuple(
        c for c in match.channels if isinstance(c, str) and 0 < len(c) <= _MAX_CHANNEL_LEN
    )[:_MAX_MATCH_CHANNELS]
    return CatalogMatch(channels=channels) if channels else None


def normalize_ranking(
    ranked: Iterable[object], entries: Sequence[mb.ManifestEntry]
) -> NormalizedRanking:
    """Validate and order a strategy's output so keyset paging over it is sound.

    Drops rows that are not :class:`RankedEntry`, have a non-finite or non-numeric
    score, or name an ``api_id`` outside ``entries``; keeps the best-scoring row per
    ``api_id``; swaps in the service's own entry (a strategy can't alter what is
    served); and sorts by ``(-score, api_id)`` — the order ``paginate_entries``
    and the cursor assume.
    """
    by_id = {e.api_id: e for e in entries}
    best: dict[str, RankedEntry] = {}
    invalid = foreign = duplicate = 0
    for row in ranked:
        if not isinstance(row, RankedEntry) or not _valid_score(row.score):
            invalid += 1
            continue
        api_id = getattr(row.entry, "api_id", None)
        own = by_id.get(api_id) if isinstance(api_id, str) else None
        if own is None:
            foreign += 1
            continue
        kept = best.get(own.api_id)
        if kept is not None:
            duplicate += 1
            if kept.score >= row.score:
                continue
        best[own.api_id] = RankedEntry(entry=own, score=row.score, match=_clean_match(row.match))
    rows = sorted(best.values(), key=lambda r: (-r.score, r.entry.api_id))
    return NormalizedRanking(
        rows=rows, dropped_invalid=invalid, dropped_foreign=foreign, dropped_duplicate=duplicate
    )
