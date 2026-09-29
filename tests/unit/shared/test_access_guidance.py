"""Unit tests for ``shared.access_guidance`` — the connect reverse-lookup.

``connect_vendor_key`` bridges two vocabularies: broker directives know the
resolved API identity axes (slugged vendor/name), while the connect surface
(``POST /integrations:connect``, ``jentic connect``, ``request_connection``)
takes the vendor-registry *key*. The lookup must decompose each registry
entry's catalog api_id exactly like the connect-session service does at
credential-create time, so a directive only ever suggests a connect that would
actually cover the denied operation.
"""

from __future__ import annotations

from jentic_one.shared.access_guidance import connect_vendor_key
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorIdentityProbeConfig,
    VendorRegistryConfig,
)

_FLOW = VendorDeviceAuthorizationFlowConfig(
    client_id="cid",
    authorization_endpoint="https://github.com/login/device/code",
    token_endpoint="https://github.com/login/oauth/access_token",
)
_PROBE = VendorIdentityProbeConfig(
    endpoint="https://api.github.com/user",
    identity_field="login",
    display_template="@{login}",
)

_REGISTRY = VendorRegistryConfig(
    entries={
        "github": VendorAuthConfig(
            vendor="github.com/api.github.com",
            display_name="GitHub",
            flows=[_FLOW],
            identity_probe=_PROBE,
        )
    }
)


def test_registry_api_reverse_maps_to_its_key() -> None:
    """The identity a connect-created credential would carry (api_vendor from
    the domain, api_name slugged from the whole api_id — the service's own
    decomposition) covers the operation's axes, so the key comes back."""
    assert (
        connect_vendor_key(
            _REGISTRY, vendor="github.com", name="github.com/api.github.com", version="1.0.0"
        )
        == "github"
    )


def test_slugged_directive_axes_match_too() -> None:
    """Directives carry the *resolved* (already slugged) identity; the lookup
    canonicalizes both sides, so the pre-slugged form maps identically."""
    assert (
        connect_vendor_key(_REGISTRY, vendor="github-com", name="github-com-api-github-com")
        == "github"
    )


def test_off_registry_api_returns_none() -> None:
    """No fabricated suggestion: an API outside the registry yields ``None``
    (callers fall back to the operator-only prose)."""
    assert connect_vendor_key(_REGISTRY, vendor="acme", name="widgets", version="1.0.0") is None


def test_same_vendor_different_api_returns_none() -> None:
    """Coverage is decided on the credential's identity, not the bare domain:
    a different API under the same vendor domain must not suggest the key."""
    assert connect_vendor_key(_REGISTRY, vendor="github.com", name="uploads.github.com") is None


def test_empty_registry_returns_none() -> None:
    assert connect_vendor_key(VendorRegistryConfig(), vendor="github.com", name="x") is None


def test_registry_entry_vendor_uses_registrable_domain() -> None:
    """The entry's vendor axis is the registrable domain of its host (the same
    derivation the catalog import applies), so a multi-label public suffix like
    ``co.uk`` keeps the company label and a sibling company does not match."""
    registry = VendorRegistryConfig(
        entries={
            "finage": VendorAuthConfig(
                vendor="api.finage.co.uk/main",
                display_name="Finage",
                flows=[_FLOW],
                identity_probe=_PROBE,
            )
        }
    )
    name = "api.finage.co.uk/main"
    assert connect_vendor_key(registry, vendor="finage.co.uk", name=name) == "finage"
    assert connect_vendor_key(registry, vendor="co.uk", name=name) is None
    assert connect_vendor_key(registry, vendor="apex27.co.uk", name=name) is None
