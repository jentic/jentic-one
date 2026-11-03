"""Factory that selects the right OIDC adapter for a config.

A tiny provider→adapter registry so new providers slot in without touching the
authorize service. Unknown/standards-compliant providers fall back to the
generic :class:`OidcAdapter`; ``google`` resolves to :class:`GoogleOidcAdapter`.

Add a provider by mapping its ``provider`` string to an ``OidcAdapter`` subclass
in :data:`_ADAPTERS` (e.g. an ``OktaOidcAdapter`` when that becomes real).
"""

from __future__ import annotations

from jentic_one.auth.core.idp.adapter import IdpAdapter
from jentic_one.auth.core.idp.oidc import GoogleOidcAdapter, OidcAdapter
from jentic_one.shared.config import IdpConfig

#: provider identifier -> adapter class. The generic OidcAdapter is the default
#: for any provider not listed here (any standards-compliant OIDC IdP).
_ADAPTERS: dict[str, type[OidcAdapter]] = {
    "google": GoogleOidcAdapter,
}


#: The adapter last built, reused while the deployment's IdP config object is
#: unchanged so its discovery document and JWKS cache live across logins.
_last_adapter: OidcAdapter | None = None


def build_idp_adapter(config: IdpConfig) -> IdpAdapter | None:
    """Build the IdP adapter for *config*, or ``None`` if IdP login is disabled.

    Selects a provider-specific adapter by ``config.provider`` (falling back to
    the generic :class:`OidcAdapter` for standard OIDC providers). Repeated
    calls with the same config object return the same adapter; replacing the
    config builds a fresh one.
    """
    global _last_adapter
    if not config.enabled:
        return None
    adapter_cls = _ADAPTERS.get(config.provider, OidcAdapter)
    cached = _last_adapter
    if cached is not None and cached.config is config and type(cached) is adapter_cls:
        return cached
    adapter = adapter_cls(config)
    _last_adapter = adapter
    return adapter
