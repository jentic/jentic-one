"""A denial for an API of a profile-scoped CLI profile suggests the profile's one connect."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jentic_one.broker.services.credentials.connect_target import resolve_connect_target
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorRegistryConfig,
)
from jentic_one.shared.schemas import APIReference


def _entry(vendor: str) -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor=vendor,
        display_name=vendor,
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="c",
                authorization_endpoint="https://github.com/login/device/code",
                token_endpoint="https://github.com/login/oauth/access_token",
            )
        ],
    )


class _NoRegistrations:
    async def list_active(self) -> tuple[Any, ...]:
        return ()


def _ctx(*keys: str) -> Any:
    vendors = {
        "github": "github.com/api.github.com",
        "github-git": "github.com/github-git",
        "stripe": "stripe.com/api.stripe.com",
    }
    entries = {k: _entry(vendors[k]) for k in keys}
    return SimpleNamespace(config=SimpleNamespace(vendors=VendorRegistryConfig(entries=entries)))


@pytest.mark.parametrize(
    "api",
    [
        APIReference(vendor="github-com", name="github-git", version="1"),
        APIReference(vendor="github-com", name="github-graphql", version="1"),
        APIReference(vendor="github-com", name="api-github-com", version="1.1.4"),
    ],
)
async def test_profile_api_suggests_the_profile_connect(api: APIReference) -> None:
    target = await resolve_connect_target(
        _ctx("github-git", "github"), api, registrations=_NoRegistrations()
    )
    assert target is not None and target.vendor_key == "github"


async def test_without_the_profile_entry_the_per_api_entry_is_suggested() -> None:
    api = APIReference(vendor="github-com", name="github-git", version="1")
    target = await resolve_connect_target(_ctx("github-git"), api, registrations=_NoRegistrations())
    assert target is not None and target.vendor_key == "github-git"


async def test_an_undeclared_api_is_not_a_profile_connect() -> None:
    api = APIReference(vendor="stripe-com", name="api-stripe-com", version="1")
    target = await resolve_connect_target(
        _ctx("stripe", "github"), api, registrations=_NoRegistrations()
    )
    assert target is not None and target.vendor_key == "stripe"
