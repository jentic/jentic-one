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

    @staticmethod
    def _key(host: str) -> str:
        # Case-insensitive like DNS itself. A trailing root dot is kept: ``host``
        # may be expanded by resolver search domains while ``host.`` is absolute,
        # so the two can legitimately resolve differently.
        return host.lower()

    def get(self, host: str) -> tuple[_IpAddress, ...] | None:
        """Return the unexpired addresses cached for *host*, or ``None``."""
        key = self._key(host)
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
        key = self._key(host)
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


def clear_resolution_cache() -> None:
    """Empty the process-wide cache (tests that swap the resolver call this)."""
    _CACHE.clear()


async def resolve_host(host: str) -> tuple[_IpAddress, ...]:
    """Resolve *host* off the event loop, reusing a recent answer when one exists.

    A cache hit returns without leaving the loop; a miss runs
    :func:`resolve_host_blocking` in a worker thread and caches a non-empty
    answer. Raises ``socket.gaierror`` when the name does not resolve (never
    cached). The returned addresses are **unvalidated** — callers must check
    each one against their egress policy.
    """
    cached = _CACHE.get(host)
    if cached is not None:
        return cached
    addrs = await asyncio.to_thread(resolve_host_blocking, host)
    _CACHE.put(host, addrs)
    return addrs
