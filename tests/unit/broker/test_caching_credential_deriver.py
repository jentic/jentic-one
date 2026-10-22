"""Unit tests for the TTL-LRU + single-flight ``CachingCredentialDeriver``."""

from __future__ import annotations

import pytest

from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.shared.broker.protocols import (
    AgentBindings,
    BoundCredential,
    CredentialDeriverProtocol,
    UnservedDiagnosis,
)


class _CountingDeriver:
    """A fake inner deriver that counts calls and returns canned values."""

    def __init__(self) -> None:
        self.binding_calls = 0
        self.diagnosis_calls = 0

    async def derive_bindings(self, *, agent_id: str) -> AgentBindings:
        self.binding_calls += 1
        return AgentBindings(
            agent_id=agent_id,
            active=(BoundCredential(credential_id=f"cred_{agent_id}", rule_set_id=None),),
        )

    async def diagnose_unserved(
        self, *, bindings: AgentBindings, vendor: str, name: str, version: str
    ) -> UnservedDiagnosis:
        self.diagnosis_calls += 1
        return UnservedDiagnosis(agent_bound_any=True, api_served=False, scope_miss=None)


def _bindings(agent_id: str) -> AgentBindings:
    return AgentBindings(agent_id=agent_id, active=())


def test_satisfies_protocol() -> None:
    assert issubclass(CachingCredentialDeriver, CredentialDeriverProtocol)


def test_rejects_nonpositive_max_entries() -> None:
    with pytest.raises(ValueError):
        CachingCredentialDeriver(_CountingDeriver(), max_entries=0)


@pytest.mark.asyncio
async def test_second_call_served_from_cache() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    r1 = await cached.derive_bindings(agent_id="agt_1")
    r2 = await cached.derive_bindings(agent_id="agt_1")
    assert inner.binding_calls == 1
    assert r1 is r2  # frozen value returned directly, no copy


@pytest.mark.asyncio
async def test_distinct_agents_not_shared() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    await cached.derive_bindings(agent_id="agt_1")
    await cached.derive_bindings(agent_id="agt_2")
    assert inner.binding_calls == 2


@pytest.mark.asyncio
async def test_diagnosis_cached_per_agent_and_api() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    api = {"vendor": "acme.com", "name": "p", "version": "v1"}
    d1 = await cached.diagnose_unserved(bindings=_bindings("agt_1"), **api)
    d2 = await cached.diagnose_unserved(bindings=_bindings("agt_1"), **api)
    await cached.diagnose_unserved(
        bindings=_bindings("agt_1"), vendor="acme.com", name="q", version="v1"
    )
    await cached.diagnose_unserved(bindings=_bindings("agt_2"), **api)
    assert d1 is d2
    assert inner.diagnosis_calls == 3


@pytest.mark.asyncio
async def test_ttl_zero_effectively_disables_reuse() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=0.0)
    await cached.derive_bindings(agent_id="agt_1")
    await cached.derive_bindings(agent_id="agt_1")
    assert inner.binding_calls == 2


@pytest.mark.asyncio
async def test_clear_drops_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    api = {"vendor": "acme.com", "name": "p", "version": "v1"}
    await cached.derive_bindings(agent_id="agt_1")
    await cached.diagnose_unserved(bindings=_bindings("agt_1"), **api)
    cached.clear()
    await cached.derive_bindings(agent_id="agt_1")
    await cached.diagnose_unserved(bindings=_bindings("agt_1"), **api)
    assert (inner.binding_calls, inner.diagnosis_calls) == (2, 2)


@pytest.mark.asyncio
async def test_lru_eviction_bounds_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0, max_entries=1)
    await cached.derive_bindings(agent_id="agt_1")
    await cached.derive_bindings(agent_id="agt_2")
    # agt_1 was evicted by agt_2 (max_entries=1) → a re-request misses.
    await cached.derive_bindings(agent_id="agt_1")
    assert inner.binding_calls == 3


@pytest.mark.asyncio
async def test_invalidate_agent_evicts_only_that_agents_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    api = {"vendor": "acme.com", "name": "p", "version": "v1"}
    for agent in ("agt_1", "agt_10"):
        await cached.derive_bindings(agent_id=agent)
        await cached.diagnose_unserved(bindings=_bindings(agent), **api)

    cached.invalidate_agent("agt_1")

    # agt_10 shares agt_1's prefix but keeps its entries.
    await cached.derive_bindings(agent_id="agt_10")
    await cached.diagnose_unserved(bindings=_bindings("agt_10"), **api)
    assert (inner.binding_calls, inner.diagnosis_calls) == (2, 2)
    await cached.derive_bindings(agent_id="agt_1")
    await cached.diagnose_unserved(bindings=_bindings("agt_1"), **api)
    assert (inner.binding_calls, inner.diagnosis_calls) == (3, 3)


@pytest.mark.asyncio
async def test_invalidate_credential_evicts_cached_entries() -> None:
    inner = _CountingDeriver()
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=300.0)
    api = {"vendor": "acme.com", "name": "p", "version": "v1"}
    await cached.derive_bindings(agent_id="agt_1")
    await cached.diagnose_unserved(bindings=_bindings("agt_2"), **api)

    cached.invalidate_credential("cred_agt_1")

    await cached.derive_bindings(agent_id="agt_1")
    await cached.diagnose_unserved(bindings=_bindings("agt_2"), **api)
    assert (inner.binding_calls, inner.diagnosis_calls) == (2, 2)
