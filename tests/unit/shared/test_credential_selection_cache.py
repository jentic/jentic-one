"""Unit tests for the per-process credential-header cache (#1714)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime

import pytest

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.credential_selection_cache import (
    CredentialHeader,
    CredentialSelectionCache,
    invalidate_all_credential_caches,
)

_HEADER = CredentialHeader(
    id="cred_1",
    name="one",
    type="no_auth",
    provider="static",
    api_vendor="v",
    api_name=None,
    api_version=None,
    server_variables=None,
    active=True,
    state="connected",
    created_by=None,
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
    provider_account_ref=None,
)


def _config(ttl: float) -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {n: {"name": n} for n in ("registry", "admin", "control")},
            "broker": {"credential_cache_ttl_s": ttl},
        }
    )


def test_context_cache_follows_the_configured_ttl() -> None:
    """``credential_cache_ttl_s: 0`` yields a disabled cache that stores nothing."""
    disabled = Context(_config(0)).credential_selection_cache
    disabled.put("v", (_HEADER,), generation=disabled.generation)
    assert not disabled.enabled
    assert disabled.get("v") is None

    ctx = Context(_config(3.0))
    assert ctx.credential_selection_cache is ctx.credential_selection_cache
    assert ctx.credential_selection_cache.enabled


def test_context_cache_follows_the_configured_size_bound() -> None:
    config = _config(3.0)
    config.broker.credential_cache_max_headers = 2
    cache = Context(config).credential_selection_cache
    cache.put("v", (_HEADER, _HEADER, _HEADER), generation=cache.generation)
    assert cache.get("v") is None


def test_put_after_an_invalidation_is_dropped() -> None:
    cache = CredentialSelectionCache(ttl_seconds=60, max_headers=100)
    generation = cache.generation
    invalidate_all_credential_caches()
    cache.put("v", (_HEADER,), generation=generation)
    assert cache.get("v") is None

    cache.put("v", (_HEADER,), generation=cache.generation)
    assert cache.get("v") == (_HEADER,)
    invalidate_all_credential_caches()
    assert cache.get("v") is None


def test_entries_are_bounded_lru() -> None:
    cache = CredentialSelectionCache(ttl_seconds=60, max_headers=2)
    for vendor in ("a", "b"):
        cache.put(vendor, (_HEADER,), generation=cache.generation)
    assert cache.get("a") is not None  # "a" becomes most recent
    cache.put("c", (_HEADER,), generation=cache.generation)
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None


def test_bound_counts_headers_not_vendors() -> None:
    """One vendor with many credentials is charged for each; an empty entry costs one."""
    other = dataclasses.replace(_HEADER, id="cred_2")
    cache = CredentialSelectionCache(ttl_seconds=60, max_headers=3)
    cache.put("empty", (), generation=cache.generation)
    cache.put("two", (_HEADER, other), generation=cache.generation)
    assert cache.get("empty") == ()
    assert cache.get("two") == (_HEADER, other)

    # A third header evicts the least recently used vendor ("empty" was read first).
    cache.put("one", (_HEADER,), generation=cache.generation)
    assert cache.get("empty") is None
    assert cache.get("two") is not None
    assert cache.get("one") is not None

    # Larger than the whole bound: not cached, and nothing else is evicted for it.
    cache.put("big", (_HEADER, other, _HEADER, other), generation=cache.generation)
    assert cache.get("big") is None
    assert cache.get("two") is not None

    # Replacing an entry releases its old cost.
    cache.put("two", (_HEADER,), generation=cache.generation)
    cache.put("again", (_HEADER,), generation=cache.generation)
    assert cache.get("two") is not None and cache.get("one") is not None


def test_max_headers_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CredentialSelectionCache(ttl_seconds=60, max_headers=0)


@pytest.mark.asyncio
async def test_single_flight_waiters_share_the_fill() -> None:
    cache = CredentialSelectionCache(ttl_seconds=60, max_headers=100)
    generation, token = cache.begin_fill("v")
    assert token is not None
    # A second filler of the same vendor gets no token: it publishes nothing.
    assert cache.begin_fill("v")[1] is None

    waiter = asyncio.create_task(cache.join_fill("v"))
    await asyncio.sleep(0)
    cache.end_fill("v", token, (_HEADER,), generation=generation)
    assert await waiter == (_HEADER,)
    assert cache.get("v") == (_HEADER,)
    assert await cache.join_fill("v") is None


@pytest.mark.asyncio
async def test_single_flight_waiters_never_get_a_fenced_read() -> None:
    """An invalidation during the fill releases waiters empty; they read the DB themselves."""
    cache = CredentialSelectionCache(ttl_seconds=60, max_headers=100)
    generation, token = cache.begin_fill("v")
    waiter = asyncio.create_task(cache.join_fill("v"))
    await asyncio.sleep(0)
    invalidate_all_credential_caches()
    assert await waiter is None
    cache.end_fill("v", token, (_HEADER,), generation=generation)
    assert cache.get("v") is None


@pytest.mark.asyncio
async def test_single_flight_failed_fill_releases_waiters() -> None:
    cache = CredentialSelectionCache(ttl_seconds=60, max_headers=100)
    generation, token = cache.begin_fill("v")
    waiter = asyncio.create_task(cache.join_fill("v"))
    await asyncio.sleep(0)
    cache.end_fill("v", token, None, generation=generation)
    assert await waiter is None
    # The vendor is free to fill again.
    assert cache.begin_fill("v")[1] is not None
