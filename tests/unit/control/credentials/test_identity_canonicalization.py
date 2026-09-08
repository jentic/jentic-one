"""Unit tests for credential API-identity canonicalization on create (#746/#775)."""

from __future__ import annotations

import pytest

from jentic_one.control.services.credentials.errors import InvalidCredentialInputError
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService


def test_canonical_scope_slugs_vendor_and_name_and_coerces_empty() -> None:
    scope = CredentialService._canonical_api_scope(
        APIReference(vendor="GitHub.com", name="", version="")
    )
    assert scope.vendor == "github-com"
    assert scope.name is None
    assert scope.version is None


def test_canonical_scope_preserves_concrete_identity() -> None:
    scope = CredentialService._canonical_api_scope(
        APIReference(vendor="GitHub.com", name="API.Name", version="1.1.4")
    )
    assert scope.vendor == "github-com"
    assert scope.name == "api-name"
    # version is trimmed, never slugified
    assert scope.version == "1.1.4"


@pytest.mark.parametrize(
    "api",
    [
        APIReference(vendor="github-com", name="openapi/main", version="1.1.4"),
        APIReference(vendor="github-com", name="openapi", version="api.github.com/main/1.1.4"),
    ],
)
def test_canonical_scope_rejects_path_shaped_identity(api: APIReference) -> None:
    with pytest.raises(InvalidCredentialInputError):
        CredentialService._canonical_api_scope(api)
