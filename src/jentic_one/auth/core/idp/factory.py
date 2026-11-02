"""Factory that selects the right OIDC adapter for a config.

A tiny provider→adapter registry so new providers slot in without touching the
authorize service. Unknown/standards-compliant providers fall back to the
generic :class:`OidcAdapter`; ``google`` resolves to :class:`GoogleOidcAdapter`.

Add a provider by mapping its ``provider`` string to an ``OidcAdapter`` subclass
in :data:`_ADAPTERS` (e.g. an ``OktaOidcAdapter`` when that becomes real).

:func:`get_idp_adapter` is what request paths call: it keeps one adapter per
config for the life of the process, so the adapter's OIDC discovery document and
JWKS cache (and the force-refresh on key rotation) persist across requests
instead of being refetched on every login.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading

from pydantic import SecretStr

from jentic_one.auth.core.idp.adapter import IdpAdapter
from jentic_one.auth.core.idp.oidc import GoogleOidcAdapter, OidcAdapter
from jentic_one.shared.cache import TTLLRU
from jentic_one.shared.config import IdpConfig

#: provider identifier -> adapter class. The generic OidcAdapter is the default
#: for any provider not listed here (any standards-compliant OIDC IdP).
_ADAPTERS: dict[str, type[OidcAdapter]] = {
    "google": GoogleOidcAdapter,
}


def build_idp_adapter(config: IdpConfig) -> IdpAdapter | None:
    """Build the IdP adapter for *config*, or ``None`` if IdP login is disabled.

    Internal to this module: request paths call :func:`get_idp_adapter`, which
    memoises what this builds.

    Selects a provider-specific adapter by ``config.provider`` (falling back to
    the generic :class:`OidcAdapter` for standard OIDC providers).
    """
    if not config.enabled:
        return None
    adapter_cls = _ADAPTERS.get(config.provider, OidcAdapter)
    return adapter_cls(config)


# One entry per distinct IdP config this process sees, which is normally one,
# so a small bound is plenty. The map is process-wide and ``TTLLRU`` does no
# locking of its own, so every access here holds ``_adapters_lock``: a sync
# dependency or a ``run_in_executor`` call can reach this from a worker thread.
_MAX_MEMOISED_ADAPTERS = 8
_adapters: TTLLRU[str, IdpAdapter | None] = TTLLRU(max_entries=_MAX_MEMOISED_ADAPTERS)
_adapters_lock = threading.Lock()


def _secret_aware(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value)


def _config_fingerprint(config: IdpConfig) -> str:
    """Digest of every config field, secrets included, so any change rebuilds the adapter."""
    payload = json.dumps(config.model_dump(), sort_keys=True, default=_secret_aware)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_idp_adapter(config: IdpConfig) -> IdpAdapter | None:
    """Return the process-wide adapter for *config*, building it on first use.

    Memoised on a fingerprint of the whole config (the way the auth discovery
    router keys its JWKS publishers), so a changed issuer, endpoint or client
    secret yields a fresh adapter while an unchanged config reuses the one
    whose discovery and JWKS caches are already warm.
    """
    key = _config_fingerprint(config)
    with _adapters_lock:
        entry = _adapters.get(key)
        if entry is not None:
            return entry.value
    # Built outside the lock (it performs no I/O, but neither does it need the
    # lock) from a private copy, so mutating the caller's config in place can
    # never change what an adapter memoised under the old fingerprint does. Two
    # threads racing the same cold key both build one; the loser's adapter is
    # simply dropped.
    adapter = build_idp_adapter(config.model_copy(deep=True))
    with _adapters_lock:
        existing = _adapters.get(key)
        if existing is not None:
            return existing.value
        _adapters.put(key, adapter, expires_at=math.inf)
    return adapter


def clear_idp_adapters() -> None:
    """Forget every memoised adapter (test isolation)."""
    with _adapters_lock:
        _adapters.clear()
