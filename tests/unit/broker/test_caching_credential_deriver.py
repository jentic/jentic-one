"""Unit tests for the TTL-LRU + single-flight ``CachingCredentialDeriver``."""

from __future__ import annotations

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
