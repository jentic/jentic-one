"""Concurrency tests for ``CachedTokenValidator`` miss coalescing (#1705).

A cache miss for a hot token — cold start or TTL expiry — must issue exactly one
resolver lookup however many requests carrying that token arrive concurrently.
The resolver here is a protocol-conforming in-memory fake that counts calls and
parks every lookup on an event, so the test controls exactly when the flight
lands.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.models import ActorType

_CONCURRENCY = 20


class _GatedResolver:
    """Counts lookups per token; each lookup blocks until ``release`` is set."""

    def __init__(self, *, resolved: Identity | None, fail: BaseException | None = None) -> None:
        self.resolved = resolved
        self.fail = fail
        self.calls: Counter[str] = Counter()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def resolve_access_token(self, token: str) -> Identity | None:
        self.calls[token] += 1
        self.started.set()
        await self.release.wait()
        if self.fail is not None:
            raise self.fail
        return self.resolved


def _identity(*, active: bool = True) -> Identity:
    return Identity(
        sub="agnt_hot",
        actor_type=ActorType.AGENT,
        permissions=[BROKER_EXECUTE_PERMISSION],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=active,
    )


async def _settle() -> None:
    """Let every spawned task run up to its first suspension point."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_concurrent_misses_for_one_token_issue_one_lookup() -> None:
    """N concurrent validations of one uncached token share a single lookup."""
    resolver = _GatedResolver(resolved=_identity())
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    tasks = [asyncio.create_task(validator.validate("at_hot")) for _ in range(_CONCURRENCY)]
    await resolver.started.wait()
    await _settle()
    resolver.release.set()
    results = await asyncio.gather(*tasks)

    assert resolver.calls == {"at_hot": 1}
    assert [r.sub for r in results] == ["agnt_hot"] * _CONCURRENCY
    # The shared verdict is cached as before: a later call is a pure hit.
    await validator.validate("at_hot")
    assert resolver.calls == {"at_hot": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolved", "reason"),
    [(None, "unknown_token"), (_identity(active=False), "token_inactive")],
)
async def test_concurrent_negative_misses_share_one_lookup_and_verdict(
    resolved: Identity | None, reason: str
) -> None:
    """Negative verdicts coalesce too; every caller still gets the uniform rejection."""
    resolver = _GatedResolver(resolved=resolved)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    tasks = [asyncio.create_task(validator.validate("at_bad")) for _ in range(_CONCURRENCY)]
    await resolver.started.wait()
    await _settle()
    resolver.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert resolver.calls == {"at_bad": 1}
    assert all(isinstance(r, TokenValidationError) and str(r) == reason for r in results)
    # The negative verdict stays cached exactly as on the uncoalesced path.
    with pytest.raises(TokenValidationError, match=reason):
        await validator.validate("at_bad")
    assert resolver.calls == {"at_bad": 1}


@pytest.mark.asyncio
async def test_distinct_tokens_are_not_coalesced() -> None:
    """Each distinct token gets its own lookup, even when they overlap in time."""
    resolver = _GatedResolver(resolved=_identity())
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    tokens = [f"at_tok_{i}" for i in range(5)]
    tasks = [asyncio.create_task(validator.validate(t)) for t in tokens for _ in range(4)]
    await resolver.started.wait()
    await _settle()
    resolver.release.set()
    await asyncio.gather(*tasks)

    assert resolver.calls == dict.fromkeys(tokens, 1)


@pytest.mark.asyncio
async def test_lookup_failure_reaches_every_waiter_and_is_not_cached() -> None:
    """A resolver exception propagates to all coalesced callers and is never cached."""
    resolver = _GatedResolver(resolved=_identity(), fail=RuntimeError("admin db down"))
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    tasks = [asyncio.create_task(validator.validate("at_hot")) for _ in range(_CONCURRENCY)]
    await resolver.started.wait()
    await _settle()
    resolver.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert resolver.calls == {"at_hot": 1}
    assert all(isinstance(r, RuntimeError) and str(r) == "admin db down" for r in results)
    assert len(validator._cache) == 0

    # The next call retries the lookup rather than replaying the failure.
    resolver.fail = None
    resolved = await validator.validate("at_hot")
    assert resolved.sub == "agnt_hot"
    assert resolver.calls == {"at_hot": 2}


@pytest.mark.asyncio
async def test_cancelling_a_waiter_does_not_disturb_the_others() -> None:
    """A cancelled follower leaves the shared lookup and every other caller intact."""
    resolver = _GatedResolver(resolved=_identity())
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    tasks = [asyncio.create_task(validator.validate("at_hot")) for _ in range(_CONCURRENCY)]
    await resolver.started.wait()
    await _settle()
    victim = tasks[_CONCURRENCY // 2]
    victim.cancel()
    await _settle()
    resolver.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert victim.cancelled()
    survivors = [r for t, r in zip(tasks, results, strict=True) if t is not victim]
    assert all(isinstance(r, Identity) and r.sub == "agnt_hot" for r in survivors)
    assert resolver.calls == {"at_hot": 1}


@pytest.mark.asyncio
async def test_cancelling_the_first_caller_does_not_re_issue_the_lookup() -> None:
    """Cancelling the caller that started the lookup does not disturb the others.

    The lookup runs in its own task, so it outlives the caller that started it:
    the remaining callers get their verdict from that same lookup rather than
    re-issuing it against the admin DB.
    """
    resolver = _GatedResolver(resolved=_identity())
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    tasks = [asyncio.create_task(validator.validate("at_hot")) for _ in range(_CONCURRENCY)]
    await resolver.started.wait()
    await _settle()
    first = tasks[0]
    first.cancel()
    await _settle()
    resolver.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert first.cancelled()
    assert all(isinstance(r, Identity) and r.sub == "agnt_hot" for r in results[1:])
    assert resolver.calls == {"at_hot": 1}
