"""Unit tests for the per-process credential-header cache (#1714)."""

from __future__ import annotations

from datetime import UTC, datetime

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


def test_put_after_an_invalidation_is_dropped() -> None:
    cache = CredentialSelectionCache(ttl_seconds=60)
    generation = cache.generation
    invalidate_all_credential_caches()
    cache.put("v", (_HEADER,), generation=generation)
    assert cache.get("v") is None

    cache.put("v", (_HEADER,), generation=cache.generation)
    assert cache.get("v") == (_HEADER,)
    invalidate_all_credential_caches()
    assert cache.get("v") is None


def test_entries_are_bounded_lru() -> None:
    cache = CredentialSelectionCache(ttl_seconds=60, max_entries=2)
    for vendor in ("a", "b"):
        cache.put(vendor, (_HEADER,), generation=cache.generation)
    assert cache.get("a") is not None  # "a" becomes most recent
    cache.put("c", (_HEADER,), generation=cache.generation)
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
