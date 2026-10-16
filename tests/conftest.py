"""Suite-wide fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jentic_one.shared import dns_cache
from jentic_one.shared.dns_cache import ResolutionCache, clear_resolution_cache


@pytest.fixture(autouse=True)
def _isolated_resolution_cache() -> Iterator[None]:
    """Start and end every test with an empty process-wide DNS answer cache.

    Tests swap ``socket.getaddrinfo`` for canned answers; a cached answer from
    one test must never satisfy a lookup in the next.
    """
    clear_resolution_cache()
    yield
    clear_resolution_cache()


@pytest.fixture()
def uncached_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every lookup hit the resolver, as once a cached answer's TTL elapses.

    Within the TTL the pre-flight check and the connect-time pin share one
    answer, so a rebind cannot land between them. Rebinding tests use this to
    feed the two lookups different answers and pin that the connect-time
    re-validation still refuses a blocked address.
    """
    monkeypatch.setattr(dns_cache, "_CACHE", ResolutionCache(ttl_s=0.0))
