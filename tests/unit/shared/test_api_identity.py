"""Unit tests for the shared API-identity slug helper."""

from __future__ import annotations

import pytest

from jentic_one.shared.models.api_identity import API_FIELD_MAX_LENGTH, slugify_api_field


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Stripe", "stripe"),
        ("Vendor.Com", "vendor-com"),
        ("Some_Name", "some-name"),
        ("My Cool API", "my-cool-api"),
        ("org/dept", "org-dept"),
        ("API@v2!beta", "api-v2-beta"),
        ("  padded  ", "padded"),
        ("--edges--", "edges"),
        ("multiple   spaces", "multiple-spaces"),
    ],
)
def test_slugify_api_field_normalizes(raw: str, expected: str) -> None:
    assert slugify_api_field(raw) == expected


def test_slugify_api_field_truncates_to_max_length() -> None:
    result = slugify_api_field("a" * 200)
    assert len(result) == API_FIELD_MAX_LENGTH
    assert result == "a" * API_FIELD_MAX_LENGTH


def test_slugify_api_field_is_idempotent() -> None:
    once = slugify_api_field("Vendor.Com")
    assert slugify_api_field(once) == once
