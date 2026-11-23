"""Unit tests for the shared catalog ``api_name`` seed helper."""

from __future__ import annotations

import pytest

from jentic_one.registry.services.catalog import manifest_builder
from jentic_one.shared.models import api_identity, credential_identity_repair


def test_catalog_api_name_aliases_are_identical_function_object() -> None:
    assert manifest_builder.catalog_api_name is api_identity.catalog_api_name
    assert credential_identity_repair.catalog_entry_api_name is api_identity.catalog_api_name
    assert manifest_builder.catalog_api_name is credential_identity_repair.catalog_entry_api_name


@pytest.mark.parametrize(
    ("api_id", "manifest_api_ids", "expected"),
    [
        ("coincap.io", ["coincap.io"], "coincap.io"),
        ("stripe.com", [], "stripe.com"),
        ("posthog.com/posthog-api", [], "posthog-api"),
        ("posthog.com/posthog-api", ["posthog.com/posthog-api"], "posthog-api"),
        (
            "posthog.com/posthog-api",
            ["posthog.com/posthog-api", "posthog.com/other", "stripe.com/posthog-api"],
            "posthog-api",
        ),
        (
            "stripe.com/checkout",
            ["stripe.com/checkout", "api.stripe.com/checkout"],
            "stripe.com/checkout",
        ),
        (
            "api.stripe.com/checkout",
            ["stripe.com/checkout", "api.stripe.com/checkout"],
            "api.stripe.com/checkout",
        ),
        (
            "stripe.com/Check_Out",
            ["stripe.com/Check_Out", "api.stripe.com/check-out"],
            "stripe.com/Check_Out",
        ),
    ],
)
def test_catalog_api_name_corpus_parity(
    api_id: str, manifest_api_ids: list[str], expected: str
) -> None:
    from_shared = api_identity.catalog_api_name(api_id, manifest_api_ids)
    from_manifest = manifest_builder.catalog_api_name(api_id, manifest_api_ids)
    from_repair = credential_identity_repair.catalog_entry_api_name(api_id, manifest_api_ids)
    assert from_shared == expected
    assert from_manifest == expected
    assert from_repair == expected
