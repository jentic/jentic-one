"""Integration tests for the Redis state backend (cross-instance coordination).

Gated on a real Redis being reachable. Provide the URL via ``JENTIC_TEST_REDIS_URL``
(e.g. ``redis://localhost:6379/0``); the whole module skips cleanly when it is
unset or the server is unreachable, so the suite stays green without Redis.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest

redis = pytest.importorskip("redis.asyncio")

from jentic_one.shared.config import SecurityConfig  # noqa: E402
from jentic_one.shared.events.repeated_failure import (  # noqa: E402
    AxisKey,
    RepeatedFailureCounter,
)
from jentic_one.shared.state.redis import RedisStateBackend  # noqa: E402

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("JENTIC_TEST_REDIS_URL")


async def _redis_available(url: str) -> bool:
    client = redis.from_url(url)
    try:
        await client.ping()
        return True
    except Exception:
        return False
    finally:
        await client.aclose()


@pytest.fixture()
async def redis_url() -> str:
    if not _REDIS_URL:
        pytest.skip("JENTIC_TEST_REDIS_URL not set; Redis integration tests skipped")
    if not await _redis_available(_REDIS_URL):
        pytest.skip(f"Redis not reachable at {_REDIS_URL}; skipping")
    return _REDIS_URL


@pytest.fixture()
async def prefix() -> str:
    """A unique prefix per test so concurrent runs never collide."""
    return f"jentic:test:{uuid.uuid4().hex}:"


@pytest.fixture()
async def backend(redis_url: str, prefix: str) -> AsyncIterator[RedisStateBackend]:
    be = RedisStateBackend(redis_url, key_prefix=prefix)
    try:
        yield be
    finally:
        await be.aclose()


async def test_two_limiters_share_one_bucket(redis_url: str, prefix: str) -> None:
    """Two backends with the SAME prefix enforce one global token bucket."""
    a = RedisStateBackend(redis_url, key_prefix=prefix)
    b = RedisStateBackend(redis_url, key_prefix=prefix)
    try:
        key = "rate:caller"
        # burst=2: between both instances only 2 should be allowed before denial.
        assert (await a.token_bucket(key, rate=0.01, burst=2)).allowed is True
        assert (await b.token_bucket(key, rate=0.01, burst=2)).allowed is True
        assert (await a.token_bucket(key, rate=0.01, burst=2)).allowed is False
        assert (await b.token_bucket(key, rate=0.01, burst=2)).allowed is False
    finally:
        await a.aclose()
        await b.aclose()


async def test_set_if_absent_second_claim_fails(backend: RedisStateBackend) -> None:
    key = f"idem:{uuid.uuid4().hex}"
    assert await backend.set_if_absent(key, b"first", ttl_s=30.0) is True
    assert await backend.set_if_absent(key, b"second", ttl_s=30.0) is False
    assert await backend.get(key) == b"first"


async def test_incr_with_ttl_accumulates(backend: RedisStateBackend) -> None:
    key = f"count:{uuid.uuid4().hex}"
    assert await backend.incr_with_ttl(key, ttl_s=30.0) == 1
    assert await backend.incr_with_ttl(key, ttl_s=30.0, amount=2) == 3


async def test_keys_carry_prefix(redis_url: str, prefix: str) -> None:
    """Every written key is stored under the configured prefix, never bare."""
    backend = RedisStateBackend(redis_url, key_prefix=prefix)
    raw = redis.from_url(redis_url)
    try:
        await backend.set(f"k:{uuid.uuid4().hex}", b"v", ttl_s=30.0)
        prefixed = [k async for k in raw.scan_iter(match=f"{prefix}*")]
        bare = [k async for k in raw.scan_iter(match="k:*")]
        assert prefixed, "expected at least one prefixed key"
        assert not bare, "no key should be written without the prefix"
    finally:
        await backend.aclose()
        await raw.aclose()


async def test_distinct_prefixes_are_isolated(redis_url: str) -> None:
    """Two backends with DIFFERENT prefixes on one Redis don't see each other."""
    p1 = f"jentic:test:{uuid.uuid4().hex}:"
    p2 = f"jentic:test:{uuid.uuid4().hex}:"
    a = RedisStateBackend(redis_url, key_prefix=p1)
    b = RedisStateBackend(redis_url, key_prefix=p2)
    try:
        await a.set("shared", b"from-a", ttl_s=30.0)
        assert await a.get("shared") == b"from-a"
        # Same logical key, different prefix → invisible to b.
        assert await b.get("shared") is None

        # An idempotency claim in one environment must not block the other.
        assert await a.set_if_absent("claim", b"a", ttl_s=30.0) is True
        assert await b.set_if_absent("claim", b"b", ttl_s=30.0) is True
    finally:
        await a.aclose()
        await b.aclose()


async def test_repeated_failure_counter_is_shared_across_instances(
    redis_url: str, prefix: str
) -> None:
    """Two broker instances on one Redis gate the repeated-failure scan on the
    cluster-wide count: failures split across replicas still reach the threshold."""
    a = RedisStateBackend(redis_url, key_prefix=prefix)
    b = RedisStateBackend(redis_url, key_prefix=prefix)
    config = SecurityConfig(execution_repeated_failure_threshold=4)
    key = AxisKey("agt_redis", "toolkit", "tk_redis", "op_redis")
    try:
        gates = [RepeatedFailureCounter(a), RepeatedFailureCounter(b)]
        decisions = [await gates[i % 2].should_scan(key, (), config) for i in range(4)]
        assert decisions == [False, False, False, True]
    finally:
        await a.aclose()
        await b.aclose()


async def test_incr_and_read_matches_the_memory_backend(redis_url: str, prefix: str) -> None:
    """The one-round-trip windowed step: increments (TTL set only on creation),
    then reads in order, with missing keys as ``None`` — same as memory."""
    backend = RedisStateBackend(redis_url, key_prefix=prefix)
    raw = redis.from_url(redis_url)
    try:
        await backend.set("marker", b"1", ttl_s=100.0)
        assert await backend.incr_and_read(["a", "b"], ["prev", "marker"], ttl_s=10.0) == (
            [1, 1],
            [None, b"1"],
        )
        assert await backend.incr_and_read(["a"], ["b"], ttl_s=50.0) == ([2], [b"1"])
        assert await backend.incr_and_read([], ["missing"], ttl_s=10.0) == ([], [None])
        # The TTL comes from the creating call, prefixed like every other key.
        assert 0 < await raw.pttl(f"{prefix}a") <= 10_000
    finally:
        await raw.aclose()
        await backend.aclose()
