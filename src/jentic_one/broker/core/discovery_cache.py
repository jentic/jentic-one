"""Short-TTL, generation-checked cache for broker route discovery.

Every broker call resolves its METHOD+URL to an operation (URL-index lookup plus
the API identity) against the registry DB. The answer only changes when a
registry write changes what the URL resolves to, so :class:`DiscoveryCache`
serves repeat lookups from memory:

- **Key.** Everything the lookup reads from the request: the upper-cased
  method, the URL's host and path, and the pinned ``revision_id`` (``None``
  means "the API's live revision"). The host is lower-cased and a default port
  (``:80`` for http, ``:443`` for https) is dropped, exactly as the lookup
  normalises it. The scheme is read only to pick which default port to strip
  and is not itself part of the key, and userinfo is dropped, because the
  lookup takes its host from ``urlparse(...).hostname`` and matches on method,
  host, path and segment count alone — so ``http://h/p`` and ``https://h/p``
  resolve identically and share one entry. The query string and fragment are
  left out because a resolution never depends on them. Keys are ``sha256``
  digests; values hold the matched operation, including its path parameters
  (raw URL path segments).
- **Freshness.** The TTL is the freshness bound. A hit is served only while its
  TTL holds *and* the process's routing generation
  (``jentic_one.shared.broker.route_generation``) still equals the one read
  before the lookup ran. A routing write advances that generation only in the
  process that commits it, and the broker always runs as its own process, so a
  deployed broker sees a promote, archive, rollback, import or delete only once
  the TTL lapses. Until then it keeps routing to the previous operation — and
  injecting that API's bound credentials — even if the API has been deleted or
  archived.
- **Misses.** A ``None`` (unmatched URL) is cached only for the shorter
  ``negative_ttl_s`` (``0`` disables it), so a newly imported API becomes
  reachable quickly. Exceptions are never cached.
- **Memory.** An LRU bound of ``max_entries``; each entry's size is bounded only
  by the request URL's length. Concurrent misses for one key share a single
  lookup (:class:`SingleFlight`).
- **Eviction is partitioned by route.** Distinct path-parameter values each take
  an entry, so entries are also bounded per route — method, host and revision,
  with the path omitted — at ``max_entries_per_route``. One caller walking a
  parameterised route with unique path parameters therefore evicts only that
  route's own entries, and cannot reach ``max_entries`` on its own while that
  per-route cap is the smaller of the two, so the routes cached for every other
  identity survive. The process-wide ``max_entries`` bound stays the memory cap
  across routes. A route's partition is dropped as soon as its last entry goes,
  so the bookkeeping is proportional to the routes currently cached. Within a
  route the cap evicts in store order — a hit re-seats the entry in the
  process-wide order only, so the hit path stays one lookup — while the
  process-wide bound evicts in access order. Both count
  ``broker.discovery_cache.evictions``.

**Tenancy.** The lookup takes no caller identity, so its answer is the same for
every caller, and one cache is shared by every identity in the process. The
caller's pin rights (``resolve_revision_pin``), authorization and egress checks
run on every request, outside this cache, whether or not it hits. The shared
cache does expose a timing signal: a hit is faster than a miss, so a caller can
tell whether *some* caller in the same process requested the exact METHOD+URL
(path parameters included) within the last TTL. That is accepted — the caller
must guess the exact URL and the window is the TTL — and is what the cache
trades for sharing one lookup across identities.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import NamedTuple
from urllib.parse import urlsplit

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.broker.protocols import ResolveResult
from jentic_one.shared.broker.route_generation import route_generation
from jentic_one.shared.metrics import get_meter

_meter = get_meter("broker")
_cache_hits = _meter.create_counter(
    "broker.discovery_cache.hits", description="Route-discovery cache hits"
)
_cache_misses = _meter.create_counter(
    "broker.discovery_cache.misses", description="Route-discovery cache misses"
)
_cache_evictions = _meter.create_counter(
    "broker.discovery_cache.evictions",
    description="Route-discovery cache entries evicted by the LRU bound",
)

#: Process-wide LRU bound on cached routes: the memory cap across every route.
DEFAULT_MAX_ENTRIES = 10_000

#: Bound on the entries one route (method, host, revision) may hold. Distinct
#: path-parameter values each take an entry, so this is what caps a spray of
#: unique URLs on one route to that route's own share of ``max_entries``.
DEFAULT_MAX_ENTRIES_PER_ROUTE = 1_000

_DEFAULT_PORT_SUFFIX = {"http": ":80", "https": ":443"}


@dataclass(frozen=True, slots=True)
class _Entry:
    value: ResolveResult | None
    generation: int
    expires_at: float
    #: The entry's route partition, so dropping it also unindexes it.
    route: str


class DiscoveryCache:
    """TTL-LRU + single-flight cache of ``resolve_operation`` results."""

    def __init__(
        self,
        *,
        ttl_s: float,
        negative_ttl_s: float = 0.0,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_entries_per_route: int = DEFAULT_MAX_ENTRIES_PER_ROUTE,
        generation: Callable[[], int] = route_generation,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("ttl_s must be finite and > 0 (build no cache to disable it)")
        # A miss never outlives a hit: a negative entry is the riskier one to
        # serve stale (it hides a route that has just gone live).
        if not math.isfinite(negative_ttl_s) or not 0 <= negative_ttl_s <= ttl_s:
            raise ValueError("negative_ttl_s must be finite, >= 0 and <= ttl_s")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if max_entries_per_route < 1:
            raise ValueError("max_entries_per_route must be >= 1")
        self._ttl_s = ttl_s
        self._negative_ttl_s = negative_ttl_s
        self._max_entries = max_entries
        # A cap at or above the process-wide bound never bites: a route can only
        # exceed it once the whole cache has, and the access-ordered bound below
        # gets there first.
        self._max_entries_per_route = max_entries_per_route
        self._generation = generation
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        # Route partition -> that route's entry keys in store order. A partition
        # exists only while it holds an entry.
        self._routes: dict[str, OrderedDict[str, None]] = {}
        self._single_flight: SingleFlight[ResolveResult | None] = SingleFlight()

    def __len__(self) -> int:
        return len(self._entries)

    async def resolve(
        self,
        *,
        method: str,
        url: str,
        revision_id: uuid.UUID | None,
        load: Callable[[], Awaitable[ResolveResult | None]],
    ) -> ResolveResult | None:
        """Return the cached resolution for the lookup, or run ``load`` and cache it."""
        keys = _cache_keys(method=method, url=url, revision_id=revision_id)
        key = keys.entry
        generation = self._generation()
        entry = self._entries.get(key)
        if entry is not None:
            if entry.generation == generation and self._clock() < entry.expires_at:
                self._entries.move_to_end(key)
                _cache_hits.add(1)
                return _copy(entry.value)
            self._discard(key)

        async def _load() -> ResolveResult | None:
            # Counted here, not before the flight: single-flighted waiters run
            # no registry lookup, so counting them as misses would report a 0 %
            # hit rate for one real lookup.
            _cache_misses.add(1)
            # Stamped with the pre-lookup time and generation, so an entry never
            # outlives its TTL measured from when its DB read began, and a read
            # that raced a routing commit is never stored.
            started = self._clock()
            value = await load()
            ttl = self._ttl_s if value is not None else self._negative_ttl_s
            if ttl > 0 and self._generation() == generation:
                self._store(
                    key,
                    _Entry(
                        value=value,
                        generation=generation,
                        expires_at=started + ttl,
                        route=keys.route,
                    ),
                )
            return value

        # The generation is part of the flight key: a lookup that starts after a
        # routing commit never joins one that started before it.
        value = await self._single_flight.do(f"{generation}:{key}", _load)
        return _copy(value)

    def _store(self, key: str, entry: _Entry) -> None:
        # Assignment appends on the miss path (``resolve`` has already dropped any
        # stale entry for the key), and re-seats an existing key as most-recent.
        self._entries[key] = entry
        self._entries.move_to_end(key)
        route = self._routes.setdefault(entry.route, OrderedDict())
        route[key] = None
        route.move_to_end(key)
        # The storing route's own cap first, so a spray of unique path parameters
        # is paid for out of that route's entries and never reaches the
        # process-wide bound on its own.
        while len(route) > self._max_entries_per_route:
            self._discard(next(iter(route)))
            _cache_evictions.add(1)
        while len(self._entries) > self._max_entries:
            self._discard(next(iter(self._entries)))
            _cache_evictions.add(1)

    def _discard(self, key: str) -> None:
        """Drop one entry and unindex it, reclaiming its route's partition when empty."""
        entry = self._entries.pop(key)
        route = self._routes.get(entry.route)
        if route is None:
            return
        route.pop(key, None)
        if not route:
            del self._routes[entry.route]


class _Keys(NamedTuple):
    """The entry's own key and the route partition it is bounded within."""

    entry: str
    route: str


def _cache_keys(*, method: str, url: str, revision_id: uuid.UUID | None) -> _Keys:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    # Userinfo is dropped and the scheme is not keyed on: the lookup reads the
    # host via ``urlparse(...).hostname`` and uses the scheme only to choose the
    # default port to strip, so two URLs that differ only in scheme or userinfo
    # resolve to the same operation and must share one entry.
    _userinfo, _at, hostport = parts.netloc.rpartition("@")
    hostport = hostport.lower()
    default_port = _DEFAULT_PORT_SUFFIX.get(scheme)
    if default_port is not None and hostport.endswith(default_port):
        hostport = hostport.removesuffix(default_port)
    revision = revision_id.hex if revision_id is not None else ""
    # The route partition is the key without the path: the one component a caller
    # varies freely, since the rest are fixed by the API the request targets.
    route = "\x00".join((method.upper(), hostport, revision))
    raw = "\x00".join((method.upper(), hostport, parts.path, revision))
    return _Keys(
        entry=hashlib.sha256(raw.encode()).hexdigest(),
        route=hashlib.sha256(route.encode()).hexdigest(),
    )


def _copy(value: ResolveResult | None) -> ResolveResult | None:
    """A per-request copy, so no caller can mutate the shared cached result."""
    if value is None:
        return None
    return replace(
        value,
        api=value.api.model_copy(),
        path_params=dict(value.path_params),
        server_variables=dict(value.server_variables),
        server_variable_defaults=dict(value.server_variable_defaults),
    )
