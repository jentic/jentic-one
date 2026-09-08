"""Unit tests for the shared API-identity slug helper and coverage seam."""

from __future__ import annotations

import pytest

from jentic_one.shared.models.api_identity import (
    API_FIELD_MAX_LENGTH,
    CredentialScope,
    canonical_credential_scope,
    credential_coverage_where,
    credential_covers,
    credential_specificity,
    slugify_api_field,
)


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


def test_canonical_scope_slugs_vendor_and_name() -> None:
    scope = canonical_credential_scope(vendor="GitHub.com", name="API.Name", version="1.1.4")
    assert scope == CredentialScope(vendor="github-com", name="api-name", version="1.1.4")


def test_canonical_scope_empty_axes_coerced_to_none() -> None:
    scope = canonical_credential_scope(vendor="stripe", name="", version="")
    assert scope == CredentialScope(vendor="stripe", name=None, version=None)


def test_canonical_scope_none_axes_stay_none() -> None:
    scope = canonical_credential_scope(vendor="stripe", name=None, version=None)
    assert scope == CredentialScope(vendor="stripe", name=None, version=None)


def test_canonical_scope_version_is_trimmed_but_not_slugified() -> None:
    # Slugifying a version would corrupt it (1.1.4 -> 1-1-4).
    scope = canonical_credential_scope(vendor="v", name=None, version="  1.1.4  ")
    assert scope.version == "1.1.4"


def test_canonical_scope_whitespace_only_axis_becomes_none() -> None:
    scope = canonical_credential_scope(vendor="v", name="   ", version="   ")
    assert scope.name is None
    assert scope.version is None


@pytest.mark.parametrize(
    ("scope", "covers"),
    [
        # vendor-only wildcard covers any concrete op for that vendor
        (CredentialScope("google", None, None), True),
        # name-scoped covers any version of that name
        (CredentialScope("google", "main", None), True),
        # fully pinned matches the exact op
        (CredentialScope("google", "main", "1"), True),
        # wrong name does not cover
        (CredentialScope("google", "other", None), False),
        # wrong version does not cover
        (CredentialScope("google", "main", "2"), False),
        # wrong vendor does not cover
        (CredentialScope("azure", None, None), False),
    ],
)
def test_credential_covers_truth_table(scope: CredentialScope, covers: bool) -> None:
    assert credential_covers(scope, vendor="google", name="main", version="1") is covers


def test_credential_covers_normalizes_operation_side() -> None:
    # A concrete op given in non-canonical form still compares canonically.
    scope = CredentialScope("github-com", "api-name", None)
    assert credential_covers(scope, vendor="GitHub.com", name="API.Name", version="1")


def test_credential_covers_version_compared_after_trim() -> None:
    scope = CredentialScope("v", None, "1.1.4")
    assert credential_covers(scope, vendor="v", name="n", version="  1.1.4  ")


def test_credential_specificity_orders_most_specific_highest() -> None:
    vendor_only = CredentialScope("v", None, None)
    name_scoped = CredentialScope("v", "n", None)
    fully_pinned = CredentialScope("v", "n", "1")
    assert credential_specificity(vendor_only) == 0
    assert credential_specificity(name_scoped) == 1
    assert credential_specificity(fully_pinned) == 2
    assert (
        credential_specificity(fully_pinned)
        > credential_specificity(name_scoped)
        > credential_specificity(vendor_only)
    )


def test_credential_specificity_version_without_name_counts_one_axis() -> None:
    assert credential_specificity(CredentialScope("v", None, "1")) == 1


def test_coverage_where_default_scopes_all_axes() -> None:
    frag = credential_coverage_where()
    assert frag == (
        "c.api_vendor = :vendor "
        "AND (c.api_name IS NULL OR c.api_name = :name) "
        "AND (c.api_version IS NULL OR c.api_version = :version)"
    )


def test_coverage_where_unscoped_reference_omits_axes() -> None:
    frag = credential_coverage_where(name_scoped=False, version_scoped=False)
    assert frag == "c.api_vendor = :vendor"
    assert ":name" not in frag
    assert ":version" not in frag


def test_coverage_where_partial_scope_keeps_named_axis_only() -> None:
    frag = credential_coverage_where(version_scoped=False)
    assert "c.api_name" in frag
    assert ":version" not in frag


def test_coverage_where_custom_alias() -> None:
    frag = credential_coverage_where(alias="cred")
    assert "cred.api_vendor = :vendor" in frag
    assert "c.api_vendor" not in frag


def test_coverage_where_no_dialect_specific_syntax() -> None:
    # Must stay portable across Postgres and SQLite: no casts / regex.
    frag = credential_coverage_where()
    assert "::" not in frag
    assert "~" not in frag
