"""Extension seam: pluggable ranking for ``GET /catalog?q=``.

**Provisional.** This is a public extension seam, but its shape may still change
before 1.0; pin the ``jentic-one`` version an extension is built against. See
``docs/development/extending-jentic-one.md`` for the author-facing guide.

A *catalog search strategy* turns a :class:`CatalogSearchRequest` (the query
plus the already-filtered manifest entries) into a ranked candidate list.
``CatalogService.list_all`` owns everything around that call — browse-vs-search
routing, filtering, validation, ordering, keyset paging, cursors and fallback —
so a strategy only ranks.

OSS ships one strategy, :class:`TokenSearchStrategy` (``name = "token"``), the
built-in token-overlap scorer. It is registered at import time and is the
default ``catalog.search_strategy``; with nothing else registered, responses
and cursors are exactly the token scorer's.

Contract for a strategy:

- ``rank(request)`` is only called for a **search**: ``request.q`` has at least
  one token under the catalog tokeniser (see
  :func:`manifest_builder.has_query_tokens`). Empty or token-free queries are
  served on the browse path and never reach a strategy.
- ``request.entries`` is already narrowed by the list filters. Return the
  **full** ranked candidate list — the service pages it, so never apply a
  limit. An empty list means "no matches".
- The service, not the strategy, owns the final order and validity: it drops
  rows whose score is not a finite ``int``/``float``, rows whose entry is not
  in ``entries`` (matched by ``api_id``; the service's own entry is served),
  and duplicate ``api_id`` rows (the best-scoring one is kept), then sorts by
  ``(-score, api_id)``. Higher scores rank first. A non-empty result with no
  valid row counts as a failure.
- Ranking must be deterministic for a fixed request: a walk re-ranks on every
  page and resumes after the cursor's exact ``(score, api_id)`` position, so
  scores must be bit-for-bit stable across calls. ``rank()`` runs once per
  page. If ranking is expensive, cache **per-entry scores** keyed by
  ``(q, manifest_revision, api_id)`` and rank ``request.entries`` from that
  map; never cache the ranked list — ``entries`` also depends on the list
  filters and on which APIs are registered or outdated, so two requests with
  the same ``q`` and revision can carry different candidates.
- Raise to signal failure. On the first page of a walk the service falls back
  to ``token`` for the whole walk; on a later page it answers 503 (the walk's
  order can't be continued by a different ranker).
- ``request.deadline_ms`` is the service-enforced budget for the call (the
  service cancels an awaiting ``rank()`` once it passes, which counts as a
  failure). Cancellation can only interrupt an ``await``: offload CPU-bound
  work (e.g. ``asyncio.to_thread``).
- ``request.options`` is reserved for request-scoped hints and is empty today;
  ignore keys you don't know. New request fields are added with defaults, so
  a strategy keeps working when the request grows.
- ``RankedEntry.match`` is optional evidence returned to clients as each
  item's ``match``. Channel names must be slugs (``^[a-z][a-z0-9_-]{0,31}$``,
  at most 8); anything else is dropped. Leave it ``None`` to omit it.

Registration mirrors the other process-global seams
(``registry/repos/search/registry.py``, ``registry/ingest/pipeline/stage_registry.py``):
call :func:`register_catalog_search_strategy` at import time, before the app
is assembled, with either a strategy **class** (built per config, through an
optional ``from_config(catalog_config, *, app_config)`` classmethod — see
:class:`ConfigurableCatalogSearchStrategy`) or a ready-made **instance**.
Select it with ``catalog.search_strategy``. App assembly fails with a
``ConfigError`` when the configured name is not registered or can't be built,
so a process never serves with a configured strategy it lacks.
``from_config`` runs synchronously while the app is assembled, before the
event loop serves requests: create network clients lazily on the first
``rank()`` call, not in ``from_config``.

A cursor records the strategy that served its walk (key ``st``). A later page
is served only by that strategy and only while it is the configured one: a
cursor naming any other strategy is rejected (400, restart the search). A
failure of the configured strategy on a later page answers 503 (retry the
page, or restart the search). Because of the boot check, a cursor naming the
configured strategy on a process where it isn't registered is a defensive
case only; it also answers 503.
"""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.shared.config import AppConfig, CatalogConfig, ConfigError
from jentic_one.shared.pagination import CATALOG_STRATEGY_NAME_RE

__all__ = [
    "TOKEN_STRATEGY_NAME",
    "CatalogMatch",
    "CatalogSearchRequest",
    "CatalogSearchStrategy",
    "ConfigurableCatalogSearchStrategy",
    "NormalizedRanking",
    "RankedEntry",
    "TokenSearchStrategy",
    "UnknownCatalogSearchStrategyError",
    "normalize_ranking",
    "register_catalog_search_strategy",
    "registered_catalog_search_strategies",
    "registered_catalog_search_strategy",
    "resolve_catalog_search_strategy",
    "validate_catalog_search_config",
]

#: Name of the built-in default strategy. Cursors from this strategy carry no
#: strategy key, so their bytes are the token scorer's.
TOKEN_STRATEGY_NAME = "token"

#: ``match`` channel names: short slugs, so strategy text can't reach clients as prose.
_CHANNEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_MAX_MATCH_CHANNELS = 8

_EMPTY_OPTIONS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class CatalogMatch:
    """Why an entry matched. Optional, returned to clients as ``match``.

    ``channels`` names the kinds of evidence behind the match (e.g.
    ``("alias",)``). It is an open set: clients must accept values they don't
    know.
    """

    channels: tuple[str, ...]


@dataclass(frozen=True)
class RankedEntry:
    """One ranked candidate: higher ``score`` first, ties broken by ``api_id``."""

    entry: mb.ManifestEntry
    score: int | float
    match: CatalogMatch | None = None


@dataclass(frozen=True, kw_only=True)
class CatalogSearchRequest:
    """Everything a strategy may use to rank one search page.

    Fields are only ever added (with defaults), so strategies keep working as
    the request grows. Construct it with keyword arguments.
    """

    #: The search text; always has at least one catalog token.
    q: str
    #: The filtered candidates, in manifest order.
    entries: Sequence[mb.ManifestEntry]
    #: Budget for this call in milliseconds (``None`` when unbounded).
    deadline_ms: int | None = None
    #: Reserved for request-scoped hints; empty today.
    options: Mapping[str, str] = field(default_factory=lambda: _EMPTY_OPTIONS)
    #: Opaque identifier of the manifest snapshot ``entries`` came from; it
    #: changes whenever the snapshot is replaced. ``None`` when unknown. Use it
    #: in a per-entry score cache key (with ``q`` and ``api_id``), never parse it.
    manifest_revision: str | None = None


@runtime_checkable
class CatalogSearchStrategy(Protocol):
    """Ranks filtered catalog entries for a search query (see the module contract)."""

    #: Stable registry key, recorded in cursors of walks this strategy served.
    name: str

    async def rank(self, request: CatalogSearchRequest) -> Sequence[RankedEntry]:
        """Return the full ranked candidate list (no limit). Raise to fall back."""
        ...


@runtime_checkable
class ConfigurableCatalogSearchStrategy(Protocol):
    """Optional construction hook for a strategy class registered by type.

    A registered class that defines ``from_config`` is built through it;
    one without it is built zero-arg. The service builds once per booted
    config and reuses the instance across requests, so construction may be
    expensive (models, indexes) but the instance must be safe to share.
    Construction runs synchronously at app assembly, before the event loop
    serves requests, so create network clients (HTTP sessions, connection
    pools) lazily on the first ``rank()`` call.
    """

    @classmethod
    def from_config(cls, config: CatalogConfig, *, app_config: AppConfig) -> CatalogSearchStrategy:
        """Build the strategy from the ``catalog`` section and the booted config.

        Reach an extension's own config section through
        ``app_config.extension(name)``. Raise to refuse the config; app assembly
        reports it as a ``ConfigError``.
        """
        ...


class TokenSearchStrategy:
    """Default strategy: the built-in token-overlap scorer, integer scores.

    Scores are passed through from ``manifest_builder.score_entries`` untouched
    (``int``), so cursors keep their integer ``s`` and existing bytes.
    """

    name = TOKEN_STRATEGY_NAME

    async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
        """Rank ``entries`` by token overlap with ``q``; non-matches are omitted."""
        return [
            RankedEntry(entry=entry, score=score)
            for entry, score in mb.score_entries(list(request.entries), request.q)
            if score is not None
        ]


class UnknownCatalogSearchStrategyError(LookupError):
    """Raised when no catalog search strategy is registered under a name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No catalog search strategy registered as {name!r}")
        self.name = name


#: A registration: a ready-made instance, or a class built per config.
_Registered = CatalogSearchStrategy | type

_STRATEGIES: dict[str, _Registered] = {TOKEN_STRATEGY_NAME: TokenSearchStrategy()}

#: Strategies built from registered classes, keyed by ``(name, id(app_config))``.
#: The config is held alongside so its ``id`` can't be reused while cached.
_BUILT: dict[tuple[str, int], tuple[AppConfig, CatalogSearchStrategy]] = {}
_MAX_BUILT = 16


def _has_async_rank(target: object) -> bool:
    return inspect.iscoroutinefunction(getattr(target, "rank", None))


def register_catalog_search_strategy(strategy: CatalogSearchStrategy | type) -> None:
    """Register a catalog search strategy (instance or class) under its ``name``.

    Call at import time, before the app is assembled. A class is built once
    per booted config (via ``from_config`` when it defines one); an instance
    is used as is. ``name`` must match ``^[a-z][a-z0-9_]{0,31}$`` and ``rank``
    must be ``async``.

    Idempotent for the same object, so a double import is safe; raises
    ``ValueError`` when the name is already bound to a different object — an
    extension can't silently shadow ``token`` or another extension's strategy.
    Same posture as ``register_strategy`` and ``register_pipeline_stage``.
    """
    name = getattr(strategy, "name", None)
    if not isinstance(name, str) or not CATALOG_STRATEGY_NAME_RE.fullmatch(name):
        msg = (
            f"Catalog search strategy name {name!r} is invalid "
            "(expected lowercase letters, digits and '_', starting with a letter, max 32)"
        )
        raise ValueError(msg)
    if not _has_async_rank(strategy):
        msg = f"Catalog search strategy {name!r} must define an async rank(request)"
        raise TypeError(msg)
    existing = _STRATEGIES.get(name)
    if existing is not None and existing is not strategy:
        msg = (
            f"Catalog search strategy {name!r} is already registered by "
            f"{_describe(existing)}; refusing to shadow it with {_describe(strategy)}"
        )
        raise ValueError(msg)
    _STRATEGIES[name] = strategy


def _describe(registered: object) -> str:
    cls = registered if isinstance(registered, type) else type(registered)
    return f"{cls.__module__}.{cls.__qualname__}"


def registered_catalog_search_strategies() -> Mapping[str, CatalogSearchStrategy | type]:
    """Snapshot of the registrations (instance or class), keyed by name."""
    return dict(_STRATEGIES)


@contextmanager
def registered_catalog_search_strategy(
    strategy: CatalogSearchStrategy | type,
) -> Iterator[None]:
    """Register ``strategy`` for the duration of a ``with`` block (test helper).

    For extension test suites: registers like
    :func:`register_catalog_search_strategy`, then removes the registration and
    any instance built from it on exit, so tests don't leak strategies into the
    process-global registry. A strategy that was already registered before the
    block stays registered.
    """
    name: str = getattr(strategy, "name", "")
    already = _STRATEGIES.get(name) is strategy
    register_catalog_search_strategy(strategy)
    try:
        yield
    finally:
        if not already:
            _STRATEGIES.pop(name, None)
            for key in [k for k in _BUILT if k[0] == name]:
                del _BUILT[key]


def resolve_catalog_search_strategy(name: str, config: AppConfig) -> CatalogSearchStrategy:
    """Return the strategy registered as ``name``, built for ``config``.

    A registered instance is returned as is. A registered class is built once
    per ``config`` object (``from_config(config.catalog, app_config=config)``
    when it defines the hook, else zero-arg) and the instance is reused.

    Raises :class:`UnknownCatalogSearchStrategyError` when nothing is registered
    under that name; anything the class's construction raises propagates, as
    does a ``TypeError`` when it doesn't produce a strategy with an async
    ``rank``.
    """
    registered = _STRATEGIES.get(name)
    if registered is None:
        raise UnknownCatalogSearchStrategyError(name)
    if not isinstance(registered, type):
        return registered
    key = (name, id(config))
    cached = _BUILT.get(key)
    if cached is not None and cached[0] is config:
        return cached[1]
    built = _build(registered, config)
    if len(_BUILT) >= _MAX_BUILT:
        del _BUILT[next(iter(_BUILT))]
    _BUILT[key] = (config, built)
    return built


def _build(cls: type, config: AppConfig) -> CatalogSearchStrategy:
    if isinstance(cls, ConfigurableCatalogSearchStrategy):
        built: object = cls.from_config(config.catalog, app_config=config)
    else:
        built = cls()
    if not isinstance(built, CatalogSearchStrategy) or not _has_async_rank(built):
        msg = f"{_describe(cls)} did not build a catalog search strategy with an async rank()"
        raise TypeError(msg)
    return built


def validate_catalog_search_config(config: AppConfig) -> None:
    """Fail app assembly when ``catalog.search_strategy`` can't serve searches.

    Raises ``ConfigError`` when the name is not registered (naming the
    registered strategies) or its class can't be built for ``config``, so a
    typo, a missing extension or a bad extension config is a boot error
    rather than a silent switch to ``token``. Builds (and caches) the
    strategy as a side effect.
    """
    name = config.catalog.search_strategy
    try:
        resolve_catalog_search_strategy(name, config)
    except UnknownCatalogSearchStrategyError:
        msg = (
            f"catalog.search_strategy {name!r} is not a registered catalog search "
            f"strategy (registered: {sorted(_STRATEGIES)})"
        )
        raise ConfigError(msg) from None
    except Exception as exc:
        msg = f"catalog.search_strategy {name!r} could not be built: {exc}"
        raise ConfigError(msg) from exc


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

    @property
    def dropped(self) -> int:
        """Total rows dropped for any reason."""
        return self.dropped_invalid + self.dropped_foreign + self.dropped_duplicate


def _valid_score(score: object) -> bool:
    if isinstance(score, bool) or not isinstance(score, int | float):
        return False
    try:
        return math.isfinite(score)
    except OverflowError:  # an int too large to convert to float
        return False


def _clean_match(match: object) -> CatalogMatch | None:
    """Keep well-formed slug channels (at most 8); drop anything else."""
    if not isinstance(match, CatalogMatch) or not isinstance(match.channels, tuple):
        return None
    channels = tuple(c for c in match.channels if isinstance(c, str) and _CHANNEL_RE.fullmatch(c))[
        :_MAX_MATCH_CHANNELS
    ]
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
