"""Unit tests for the TTL-LRU + single-flight ``CachingCredentialDeriver``."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.shared.broker.protocols import (
    BoundCredential,
    CredentialDerivation,
    CredentialDeriverProtocol,
)


class _CountingDeriver:
    """A fake inner deriver that counts calls and returns a canned derivation."""

    def __init__(self) -> None:
        self.calls = 0

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        self.calls += 1
        return CredentialDerivation(
            credentials=(BoundCredential(credential_id=f"cred_{agent_id}", rule_set_id=None),),
            agent_bound_any=True,
            api_served=True,
            identity_mismatch=None,
        )


def test_satisfies_protocol() -> None:
    assert issubclass(CachingCredentialDeriver, CredentialDeriverProtocol)


def test_rejects_nonpositive_max_entries() -> None:
    with pytest.raises(ValueError):
        CachingCredentialDeriver(_CountingDeriver(), max_entries=0)


@pytest.mark.asyncio
async def test_second_call_served_from_cache() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    kwargs = {"agent_id": "agt_1", "vendor": "acme.com", "name": "pets", "version": "v1"}
    r1 = await cached.derive_credentials(**kwargs)
    r2 = await cached.derive_credentials(**kwargs)
    assert inner.calls == 1
    assert r1 is r2  # frozen value returned directly, no copy


@pytest.mark.asyncio
async def test_distinct_keys_not_shared() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    await cached.derive_credentials(agent_id="agt_2", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_ttl_zero_effectively_disables_reuse() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=0.0)
    kwargs = {"agent_id": "agt_1", "vendor": "acme.com", "name": "p", "version": "v1"}
    await cached.derive_credentials(**kwargs)
    await cached.derive_credentials(**kwargs)
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_clear_drops_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    kwargs = {"agent_id": "agt_1", "vendor": "acme.com", "name": "p", "version": "v1"}
    await cached.derive_credentials(**kwargs)
    cached.clear()
    await cached.derive_credentials(**kwargs)
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_lru_eviction_bounds_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0, max_entries=1)
    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    await cached.derive_credentials(agent_id="agt_2", vendor="acme.com", name="p", version="v1")
    # agt_1 was evicted by agt_2 (max_entries=1) → a re-request misses.
    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_invalidate_agent_evicts_only_that_agents_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    await cached.derive_credentials(agent_id="agt_2", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 2

    cached.invalidate_agent("agt_1")

    await cached.derive_credentials(agent_id="agt_2", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 2
    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_invalidate_credential_evicts_cached_derivations() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 1

    cached.invalidate_credential("cred_agt_1")

    await cached.derive_credentials(agent_id="agt_1", vendor="acme.com", name="p", version="v1")
    assert inner.calls == 2


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _ExpiringDeriver(_CountingDeriver):
    def __init__(self, valid_until: datetime | None) -> None:
        super().__init__()
        self._valid_until = valid_until

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        base = await super().derive_credentials(
            agent_id=agent_id, vendor=vendor, name=name, version=version
        )
        return replace(base, valid_until=self._valid_until)


_KW = {"agent_id": "agt_1", "vendor": "acme.com", "name": "pets", "version": "v1"}
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_entry_not_served_at_or_past_valid_until() -> None:
    clock = _Clock(_T0)
    inner = _ExpiringDeriver(_T0 + timedelta(seconds=10))
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0, clock=clock)
    await cached.derive_credentials(**_KW)
    clock.now = _T0 + timedelta(seconds=9)
    await cached.derive_credentials(**_KW)
    assert inner.calls == 1  # still served inside valid_until
    inner._valid_until = None  # the next database read has no expiring candidate
    clock.now = _T0 + timedelta(seconds=10)  # exactly at valid_until
    await cached.derive_credentials(**_KW)
    assert inner.calls == 2  # refetched despite TTL not lapsing


@pytest.mark.asyncio
async def test_no_valid_until_uses_ttl_only() -> None:
    clock = _Clock(_T0)
    inner = _ExpiringDeriver(None)
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0, clock=clock)
    await cached.derive_credentials(**_KW)
    clock.now = _T0 + timedelta(days=365)
    await cached.derive_credentials(**_KW)
    assert inner.calls == 1


async def test_in_flight_lookup_crossing_deadline_is_rederived_for_every_waiter() -> None:
    clock = _Clock(_T0)
    started, release = asyncio.Event(), asyncio.Event()

    class SlowDeriver(_CountingDeriver):
        async def derive_credentials(self, **kwargs: str) -> CredentialDerivation:
            result = await super().derive_credentials(**kwargs)
            if self.calls == 1:
                started.set()
                await release.wait()
                return replace(result, valid_until=_T0 + timedelta(seconds=1))
            return replace(result, credentials=())

    inner = SlowDeriver()
    cached = CachingCredentialDeriver(inner, clock=clock)
    leader = asyncio.create_task(cached.derive_credentials(**_KW))
    await started.wait()
    clock.now += timedelta(seconds=1)
    waiter = asyncio.create_task(cached.derive_credentials(**_KW))
    await asyncio.sleep(0)
    release.set()
    for result in await asyncio.gather(leader, waiter):
        assert not result.credentials


@pytest.mark.asyncio
async def test_use_counted_derivation_is_returned_once_and_never_cached() -> None:
    """A use count must reach the counter on every call: not kept, but not re-derived in a loop."""
    clock = _Clock(_T0)

    class UseCountedDeriver(_CountingDeriver):
        async def derive_credentials(self, **kwargs: str) -> CredentialDerivation:
            result = await super().derive_credentials(**kwargs)
            return replace(result, cacheable=False, valid_until=_T0 + timedelta(days=1))

    inner = UseCountedDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0, clock=clock)
    first = await asyncio.wait_for(cached.derive_credentials(**_KW), timeout=1)
    second = await asyncio.wait_for(cached.derive_credentials(**_KW), timeout=1)
    assert first.credentials and second.credentials
    assert inner.calls == 2  # one read per call: never served from the cache
