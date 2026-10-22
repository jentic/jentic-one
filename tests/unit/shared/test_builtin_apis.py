"""The built-in APIs a vendor-registry entry can target (the GitHub git API)."""

from __future__ import annotations

from jentic_one.broker.core.git_api import GITHUB_GIT_API
from jentic_one.shared.access_guidance import (
    catalog_api_id_covers,
    catalog_import_name,
    connect_vendor_key,
)
from jentic_one.shared.builtin_apis import (
    GITHUB_GIT_CATALOG_API_ID,
    is_builtin_catalog_api_id,
)
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorRegistryConfig,
)
from jentic_one.shared.models.api_identity import canonical_credential_scope, credential_covers
from jentic_one.shared.vendor_domain import vendor_from_api_id


def _entry(vendor: str) -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor=vendor,
        display_name=vendor,
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="client",
                authorization_endpoint="https://github.com/login/device/code",
                token_endpoint="https://github.com/login/oauth/access_token",
            )
        ],
    )


def test_the_git_catalog_id_decomposes_to_the_git_api_identity() -> None:
    # The vendor connect derives a not-yet-imported API's scope this way, so a
    # credential connected through a ``github.com/github-git`` entry covers
    # the broker's git API, and only it.
    scope = canonical_credential_scope(
        vendor=vendor_from_api_id(GITHUB_GIT_CATALOG_API_ID) or "",
        name=catalog_import_name(GITHUB_GIT_CATALOG_API_ID),
        version=None,
    )
    assert (scope.vendor, scope.name, scope.version) == (
        GITHUB_GIT_API.vendor,
        GITHUB_GIT_API.name,
        None,
    )
    assert credential_covers(
        scope,
        vendor=GITHUB_GIT_API.vendor,
        name=GITHUB_GIT_API.name,
        version=GITHUB_GIT_API.version,
    )
    assert not credential_covers(scope, vendor="github-com", name="api-github-com", version="1.1.4")
    assert catalog_api_id_covers(
        GITHUB_GIT_CATALOG_API_ID,
        vendor=GITHUB_GIT_API.vendor,
        name=GITHUB_GIT_API.name,
        version=GITHUB_GIT_API.version,
    )


def test_a_git_denial_suggests_the_git_entry_and_a_rest_denial_the_rest_entry() -> None:
    vendors = VendorRegistryConfig(
        entries={
            "github": _entry("github.com/api.github.com"),
            "github-git": _entry(GITHUB_GIT_CATALOG_API_ID),
        }
    )
    assert (
        connect_vendor_key(
            vendors,
            vendor=GITHUB_GIT_API.vendor,
            name=GITHUB_GIT_API.name,
            version=GITHUB_GIT_API.version,
        )
        == "github-git"
    )
    assert (
        connect_vendor_key(vendors, vendor="github-com", name="api-github-com", version="1.1.4")
        == "github"
    )
    without_git = VendorRegistryConfig(entries={"github": _entry("github.com/api.github.com")})
    assert (
        connect_vendor_key(
            without_git,
            vendor=GITHUB_GIT_API.vendor,
            name=GITHUB_GIT_API.name,
            version=GITHUB_GIT_API.version,
        )
        is None
    )


def test_is_builtin_catalog_api_id() -> None:
    assert is_builtin_catalog_api_id("github.com/github-git")
    assert is_builtin_catalog_api_id(" GitHub.com/GitHub-Git ")
    assert not is_builtin_catalog_api_id("github.com/api.github.com")
