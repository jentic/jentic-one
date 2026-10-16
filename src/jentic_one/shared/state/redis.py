"""Redis-backed shared-state backend (cross-instance coordination).

This module is imported **lazily** by the factory only when
``BackendKind.REDIS`` is selected, so the default memory path pulls in no
``redis`` dependency. The ``import redis.asyncio`` therefore lives at module
top level here (this module is the redis path).

Every key operation routes through :meth:`RedisStateBackend._k`, which prepends
the configured ``key_prefix``. This is the multi-tenant blast-radius guard:
enterprises routinely point Dev/Staging/Prod at one HA Redis cluster, and bare
keys would collide across deployments (a staging idempotency replay served to
prod, rate-limit buckets bleeding, circuit state cross-contaminating). The
prefix is owned by the backend, not each call site, so a new feature cannot
forget it.
"""

from __future__ import annotations

from typing import cast

import redis.asyncio as redis

from jentic_one.shared.state.backend import RateLimitDecision

# Atomic token-bucket refill-and-consume. Keyed by a single bucket key holding a
# hash of {tokens, ts}. Returns {allowed, remaining, retry_after_ms}. Running
# this server-side keeps the read-refill-write sequence atomic across instances.
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now_ms = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = burst
  ts = now_ms
end

local elapsed = math.max(0, now_ms - ts) / 1000.0
tokens = math.min(burst, tokens + elapsed * rate)

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
else
  local deficit = cost - tokens
  if rate > 0 then
    retry_after_ms = math.ceil(deficit / rate * 1000.0)
  else
    retry_after_ms = -1
  end
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now_ms)
redis.call('PEXPIRE', key, ttl_ms)

return {allowed, math.floor(tokens), retry_after_ms}
"""

# Atomic increment that sets the TTL only when creating the counter, so a
# sliding burst cannot indefinitely extend the window.
_INCR_WITH_TTL_LUA = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local value = redis.call('INCRBY', key, amount)
if value == amount then
  redis.call('PEXPIRE', key, ttl_ms)
end
return value
"""

# Compare-and-delete: drop the key only while it still holds the caller's value,
# so a holder whose claim expired can never delete a newer claim on the same key.
_DELETE_IF_EQUALS_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisStateBackend:
    """Redis implementation of all three shared-state roles.

    Args:
        url: Redis connection URL (e.g. ``redis://host:6379/0``).
        key_prefix: Mandatory per-environment prefix prepended to every key.
    """

    def __init__(self, url: str, *, key_prefix: str) -> None:
        self._key_prefix = key_prefix
        self._redis: redis.Redis = redis.from_url(url)
        self._token_bucket_script = self._redis.register_script(_TOKEN_BUCKET_LUA)
        self._incr_script = self._redis.register_script(_INCR_WITH_TTL_LUA)
        self._delete_if_equals_script = self._redis.register_script(_DELETE_IF_EQUALS_LUA)

    def _k(self, key: str) -> str:
        """Prepend the configured prefix. The ONLY way keys reach Redis."""
        return f"{self._key_prefix}{key}"

    async def get(self, key: str) -> bytes | None:
        result = await self._redis.get(self._k(key))
        # decode_responses defaults to False, so values come back as bytes.
        return cast("bytes | None", result)

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        await self._redis.set(self._k(key), value, px=_ms(ttl_s))

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        result = await self._redis.set(self._k(key), value, px=_ms(ttl_s), nx=True)
        return bool(result)

    async def delete_if_equals(self, key: str, expected: bytes) -> bool:
        result = await self._delete_if_equals_script(keys=[self._k(key)], args=[expected])
        return bool(result)

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int:
        # _INCR_WITH_TTL_LUA uses the value == amount heuristic to detect a freshly
        # created counter and (re)set the TTL only then. A zero/negative amount
        # would break that heuristic (and could decrement), so require positive.
        if amount <= 0:
            raise ValueError(f"incr_with_ttl amount must be positive, got {amount}")
        result = await self._incr_script(keys=[self._k(key)], args=[amount, _ms(ttl_s)])
        return int(result)

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision:
        now_ms = await self._now_ms()
        # Keep bucket state alive for the worst-case full-refill window plus slack.
        ttl_ms = _bucket_ttl_ms(rate=rate, burst=burst)
        allowed, remaining, retry_after_ms = await self._token_bucket_script(
            keys=[self._k(key)],
            args=[rate, burst, cost, now_ms, ttl_ms],
        )
        retry_after_s = float("inf") if retry_after_ms < 0 else retry_after_ms / 1000.0
        return RateLimitDecision(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after_s=retry_after_s,
            limit=burst,
        )

    async def _now_ms(self) -> int:
        """Server clock in milliseconds, so all instances share one time base."""
        seconds, microseconds = await self._redis.time()
        return seconds * 1000 + microseconds // 1000

    async def aclose(self) -> None:
        await self._redis.aclose()


def _ms(seconds: float) -> int:
    """Convert seconds to whole milliseconds (at least 1ms for any positive TTL)."""
    return max(1, int(seconds * 1000))


def _bucket_ttl_ms(*, rate: float, burst: int) -> int:
    """TTL that comfortably outlives a full refill so idle buckets eventually evict."""
    refill_s = burst / rate if rate > 0 else 60.0
    return _ms(refill_s * 2 + 60.0)
