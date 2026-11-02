"""Bounded "seen recently" window: accept each key at most once per TTL.

Used for in-process throttles and log de-duplication, where losing the window
(process restart, LRU eviction under a flood of distinct keys) only admits one
extra accept. The map is bounded by ``max_keys`` with least-recently-accepted
eviction, so a hostile spray of unique keys cannot grow it without limit.
Thread-safe: the lock makes :meth:`RecentKeys.try_accept` atomic across threads
as well as across tasks.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable

__all__ = ["RecentKeys"]


class RecentKeys[K: Hashable]:
    """Accepts a key when it was not accepted within the last ``ttl_seconds``."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        self._ttl_seconds = ttl_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._accepted_at: OrderedDict[K, float] = OrderedDict()

    def try_accept(self, key: K) -> bool:
        """Record ``key`` and return ``True`` unless it was accepted within the window.

        A rejected call leaves the key's window where it was: repeated attempts
        never extend it.
        """
        now = self._clock()
        with self._lock:
            last = self._accepted_at.get(key)
            if last is not None and now - last < self._ttl_seconds:
                return False
            self._accepted_at[key] = now
            self._accepted_at.move_to_end(key)
            while len(self._accepted_at) > self._max_keys:
                self._accepted_at.popitem(last=False)
            return True

    def release(self, key: K) -> None:
        """Forget ``key`` so the next :meth:`try_accept` for it succeeds."""
        with self._lock:
            self._accepted_at.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._accepted_at.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._accepted_at)
