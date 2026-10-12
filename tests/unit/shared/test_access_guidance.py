"""Unit tests for ``shared.access_guidance`` — the connect reverse-lookups.

``connect_vendor_key`` bridges two vocabularies: broker directives know the
resolved API identity axes (slugged vendor/name), while the connect surface
(``POST /integrations:connect``, ``jentic connect``, ``request_connection``)
takes the vendor-registry *key*. The lookup must match each registry entry's
catalog api_id against every identity a catalog import of it can register
under, so a directive only ever suggests a connect that would actually cover
the denied operation.
"""

from __future__ import annotations

from jentic_one.shared.access_guidance import (
    ConnectTarget,
    catalog_api_id_covers,
    catalog_import_name,
    connect_target,
    connect_vendor_key,
)
from jentic_one.shared.broker.protocols import ConnectableRegistration
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
    """An API carrying the whole-id name (a clashing catalog entry, or one
    imported before the sub-segment naming) still maps back to the key."""
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


def test_catalog_import_name_is_the_sub_segment() -> None:
    """A catalog import names a ``domain/sub`` API by its sub segment; a bare
    domain keeps the whole id."""
    assert catalog_import_name("github.com/api.github.com") == "api.github.com"
    assert catalog_import_name("coincap.io") == "coincap.io"
    assert catalog_import_name("coincap.io/") == "coincap.io/"


def test_registry_entry_covers_the_catalog_imported_identity() -> None:
    """A catalog import registers ``github.com/api.github.com`` as
    ``github-com/api-github-com``; the config entry's key comes back for it,
    raw or slugged."""
    assert (
        connect_vendor_key(_REGISTRY, vendor="github-com", name="api-github-com", version="1.0.0")
        == "github"
    )
    assert connect_vendor_key(_REGISTRY, vendor="github.com", name="api.github.com") == "github"


def test_sub_segment_match_still_requires_the_vendor_axis() -> None:
    """The sub-segment name alone is not enough: the same sub under another
    vendor domain is a different API."""
    assert connect_vendor_key(_REGISTRY, vendor="gitlab-com", name="api-github-com") is None
    assert not catalog_api_id_covers(
        "github.com/api.github.com", vendor="github-com", name="uploads-github-com", version="v1"
    )


def test_bare_domain_entry_covers_its_whole_id_name() -> None:
    """A bare-domain catalog id seeds its whole id as the name."""
    assert catalog_api_id_covers("coincap.io", vendor="coincap-io", name="coincap-io", version="2")


_GMAIL = ConnectableRegistration(
    id="oar_gmail", api_vendor="google", catalog_api_id="googleapis.com/gmail"
)
_DRIVE = ConnectableRegistration(
    id="oar_drive", api_vendor="google", catalog_api_id="googleapis.com/drive"
)


def test_connect_target_config_entry_wins_without_a_pin() -> None:
    """A covering config entry answers first: an unpinned ``:connect <key>``
    resolves to the config entry before any registration sharing the key."""
    shadow = ConnectableRegistration(
        id="oar_gh", api_vendor="github", catalog_api_id="github.com/api.github.com"
    )
    assert connect_target(
        _REGISTRY, [shadow], vendor="github.com", name="github.com/api.github.com"
    ) == ConnectTarget(vendor_key="github")


def test_connect_target_single_registration_is_pinned() -> None:
    """Registrations match by ``catalog_api_id`` on the credential coverage
    rule, so a sibling registration for another API under the same connect
    key does not count — the one match is pinned by id."""
    target = connect_target(
        VendorRegistryConfig(),
        [_GMAIL, _DRIVE],
        vendor="googleapis-com",
        name="googleapis-com-gmail",
        version="v1",
    )
    assert target == ConnectTarget(vendor_key="google", registration_id="oar_gmail")
    assert target.as_parameters() == {"vendor_key": "google", "registration_id": "oar_gmail"}


def test_connect_target_several_registrations_emit_no_pin() -> None:
    """Several shared apps covering the API: the key is still suggested, but
    choosing an app is operator policy, so no ``registration_id``."""
    second = ConnectableRegistration(
        id="oar_gmail_b", api_vendor="google", catalog_api_id="googleapis.com/gmail"
    )
    target = connect_target(
        VendorRegistryConfig(),
        [second, _GMAIL],
        vendor="googleapis.com",
        name="googleapis.com/gmail",
    )
    assert target == ConnectTarget(vendor_key="google")
    assert target.as_parameters() == {"vendor_key": "google"}


def test_connect_target_nothing_covers_returns_none() -> None:
    assert (
        connect_target(_REGISTRY, [_DRIVE], vendor="googleapis.com", name="googleapis.com/gmail")
        is None
    )
    assert connect_target(VendorRegistryConfig(), [], vendor="acme", name="widgets") is None


def test_connect_target_pins_a_registration_for_the_catalog_imported_identity() -> None:
    """One shared app for ``googleapis.com/gmail`` covers the API its catalog
    import registers (``googleapis-com/gmail``), so the directive pins it."""
    target = connect_target(
        VendorRegistryConfig(),
        [_GMAIL, _DRIVE],
        vendor="googleapis-com",
        name="gmail",
        version="v1",
    )
    assert target == ConnectTarget(vendor_key="google", registration_id="oar_gmail")
    assert not catalog_api_id_covers(
        "googleapis.com/drive", vendor="googleapis-com", name="gmail", version="v1"
    )
