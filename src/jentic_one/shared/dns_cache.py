"""Off-loop host resolution with a short-lived, bounded cache of raw DNS answers.

The broker resolves an upstream host twice per call: the pre-flight SSRF check
(``validate_upstream_url_async`` in ``shared/url_validation``) and the
connection-time DNS-pinning transport (``shared/egress``). :func:`resolve_host`
serves both, so one lookup answers the pair, and it runs the blocking
``socket.getaddrinfo`` in a worker thread so a slow resolver never stalls the
event loop.

Security contract: this module caches **raw resolver answers only** — it never
caches an allow/deny decision. Every consumer validates every returned address
against its own egress policy on each use (``assert_ip_allowed``), so a cached
entry can't widen what any policy permits, and the pinning transport still
connects only to an IP it has just validated. Failed and empty resolutions are
never cached, so a name that does not resolve is retried (and the pinning
transport fails closed on it). Entries live for :data:`RESOLUTION_TTL_S` seconds
at most, which bounds how stale a pinned address can be; the cache holds at most
:data:`RESOLUTION_CACHE_MAX_ENTRIES` hosts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Upper bound on how long a resolver answer is reused, in seconds.
RESOLUTION_TTL_S = 5.0

#: Upper bound on the number of cached hosts (oldest evicted first).
RESOLUTION_CACHE_MAX_ENTRIES = 1024


def resolve_host_blocking(host: str) -> tuple[_IpAddress, ...]:
    """Resolve *host* for TCP with the blocking system resolver (no cache).

    Raises ``socket.gaierror`` when the name does not resolve. May return an
    empty tuple; callers decide whether that is a failure.
    """
    results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return tuple(ipaddress.ip_address(sockaddr[0]) for *_rest, sockaddr in results)


def _cache_key(host: str) -> str:
    # Case-insensitive like DNS itself. A trailing root dot is kept: ``host``
    # may be expanded by resolver search domains while ``host.`` is absolute,
    # so the two can legitimately resolve differently.
    return host.lower()


class ResolutionCache:
    """Thread-safe TTL + size-bounded map of host -> resolved addresses."""

    def __init__(
        self,
        *,
        ttl_s: float = RESOLUTION_TTL_S,
        max_entries: int = RESOLUTION_CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, tuple[_IpAddress, ...]]] = OrderedDict()

    def get(self, host: str) -> tuple[_IpAddress, ...] | None:
        """Return the unexpired addresses cached for *host*, or ``None``."""
        key = _cache_key(host)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, addrs = entry
            if now >= expires_at:
                del self._entries[key]
                return None
            return addrs

    def put(self, host: str, addrs: tuple[_IpAddress, ...]) -> None:
        """Cache a non-empty answer for *host* (empty answers are ignored)."""
        if not addrs or self._ttl_s <= 0 or self._max_entries <= 0:
            return
        key = _cache_key(host)
        expires_at = self._clock() + self._ttl_s
        with self._lock:
            self._entries[key] = (expires_at, addrs)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every cached entry."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_CACHE = ResolutionCache()

# In-flight lookups, per event loop then per cache key. A future is bound to the
# loop that created it, so callers on different loops never share one; the weak
# outer key drops a closed loop's map with the loop.
_IN_FLIGHT: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Future[tuple[_IpAddress, ...]]]
] = weakref.WeakKeyDictionary()

# Bumped by :func:`clear_resolution_cache` so a lookup started before a clear
# never repopulates the cache after it.
_generation = 0


def clear_resolution_cache() -> None:
    """Empty the process-wide cache (tests that swap the resolver call this).

    Lookups still in flight finish for their current waiters but neither cache
    their answer nor serve later callers.
    """
    global _generation
    _generation += 1
    _CACHE.clear()
    _IN_FLIGHT.clear()


def _start_lookup(
    loop: asyncio.AbstractEventLoop,
    pending: dict[str, asyncio.Future[tuple[_IpAddress, ...]]],
    key: str,
    host: str,
) -> asyncio.Future[tuple[_IpAddress, ...]]:
    future: asyncio.Future[tuple[_IpAddress, ...]] = loop.run_in_executor(
        None, resolve_host_blocking, host
    )
    generation = _generation

    def _settle(done: asyncio.Future[tuple[_IpAddress, ...]]) -> None:
        # Remove only our own entry so a failed lookup is retried by the next
        # caller instead of being replayed to it.
        if pending.get(key) is done:
            del pending[key]
        if done.cancelled():
            return
        # Retrieving the exception marks it handled when every waiter has gone.
        if done.exception() is None and generation == _generation:
            _CACHE.put(host, done.result())

    future.add_done_callback(_settle)
    pending[key] = future
    return future


async def resolve_host(host: str) -> tuple[_IpAddress, ...]:
    """Resolve *host* off the event loop, reusing a recent answer when one exists.

    A cache hit returns without leaving the loop. On a miss, concurrent callers
    for the same host share a single :func:`resolve_host_blocking` run in the
    default executor (so a TTL expiry under load costs one worker thread, not
    one per request); a non-empty answer is cached. Each caller awaits the
    shared lookup through :func:`asyncio.shield`, so cancelling one caller
    never cancels it for the others. Raises ``socket.gaierror`` to every
    waiter when the name does not resolve; failures are never cached and the
    next call retries. The returned addresses are **unvalidated** — callers
    must check each one against their egress policy.
    """
    cached = _CACHE.get(host)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    pending = _IN_FLIGHT.setdefault(loop, {})
    key = _cache_key(host)
    future = pending.get(key)
    if future is None:
        future = _start_lookup(loop, pending, key, host)
    return await asyncio.shield(future)
