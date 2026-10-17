"""Short-TTL, generation-checked cache for broker route discovery.

Every broker call resolves its METHOD+URL to an operation (URL-index lookup plus
the API identity) against the registry DB. The answer only changes when a
registry write changes what the URL resolves to, so :class:`DiscoveryCache`
serves repeat lookups from memory:

- **Key.** Everything the lookup reads from the request: the upper-cased
  method, the URL's scheme, authority and path, and the pinned ``revision_id``
  (``None`` means "the API's live revision"). The scheme and host are
  lower-cased and a default port (``:80`` for http, ``:443`` for https) is
  dropped, exactly as the lookup normalises them; the query string and fragment
  are left out because a resolution never depends on them. Keys are ``sha256``
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

#: LRU bound on cached routes. Distinct path-parameter values each take an entry,
#: so the bound (not the TTL) is what caps memory under a spray of unique URLs.
DEFAULT_MAX_ENTRIES = 10_000

_DEFAULT_PORT_SUFFIX = {"http": ":80", "https": ":443"}


@dataclass(frozen=True, slots=True)
class _Entry:
    value: ResolveResult | None
    generation: int
    expires_at: float


class DiscoveryCache:
    """TTL-LRU + single-flight cache of ``resolve_operation`` results."""

    def __init__(
        self,
        *,
        ttl_s: float,
        negative_ttl_s: float = 0.0,
        max_entries: int = DEFAULT_MAX_ENTRIES,
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
        self._ttl_s = ttl_s
        self._negative_ttl_s = negative_ttl_s
        self._max_entries = max_entries
        self._generation = generation
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
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
        key = _cache_key(method=method, url=url, revision_id=revision_id)
        generation = self._generation()
        entry = self._entries.get(key)
        if entry is not None:
            if entry.generation == generation and self._clock() < entry.expires_at:
                self._entries.move_to_end(key)
                _cache_hits.add(1)
                return _copy(entry.value)
            del self._entries[key]

        async def _load() -> ResolveResult | None:
            # Stamped with the pre-lookup time and generation, so an entry never
            # outlives its TTL measured from when its DB read began, and a read
            # that raced a routing commit is never stored.
            started = self._clock()
            value = await load()
            ttl = self._ttl_s if value is not None else self._negative_ttl_s
            if ttl > 0 and self._generation() == generation:
                entry = _Entry(value=value, generation=generation, expires_at=started + ttl)
                self._store(key, entry)
            return value

        _cache_misses.add(1)
        # The generation is part of the flight key: a lookup that starts after a
        # routing commit never joins one that started before it.
        value = await self._single_flight.do(f"{generation}:{key}", _load)
        return _copy(value)

    def _store(self, key: str, entry: _Entry) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            _cache_evictions.add(1)


def _cache_key(*, method: str, url: str, revision_id: uuid.UUID | None) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    userinfo, at, hostport = parts.netloc.rpartition("@")
    hostport = hostport.lower()
    default_port = _DEFAULT_PORT_SUFFIX.get(scheme)
    if default_port is not None and hostport.endswith(default_port):
        hostport = hostport.removesuffix(default_port)
    raw = "\x00".join(
        (
            method.upper(),
            scheme,
            f"{userinfo}{at}{hostport}",
            parts.path,
            revision_id.hex if revision_id is not None else "",
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()


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
