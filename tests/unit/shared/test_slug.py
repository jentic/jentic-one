"""Tests for the shared vendor/name slug normalization (issue #656)."""

from jentic_one.shared.slug import slugify_identifier


def test_dots_become_hyphens() -> None:
    assert slugify_identifier("httpbin.org") == "httpbin-org"
    assert slugify_identifier("github.com") == "github-com"
    assert slugify_identifier("atlassian.com") == "atlassian-com"


def test_lowercased_and_stripped() -> None:
    assert slugify_identifier("  Big Corp  ") == "big-corp"


def test_special_chars_collapse_to_single_hyphen() -> None:
    assert slugify_identifier("org/dept") == "org-dept"
    assert slugify_identifier("API@v2!beta") == "api-v2-beta"


def test_leading_trailing_hyphens_stripped() -> None:
    assert slugify_identifier(".leading") == "leading"
    assert slugify_identifier("trailing.") == "trailing"


def test_length_capped_at_100() -> None:
    assert len(slugify_identifier("a" * 200)) == 100


def test_already_normalized_is_idempotent() -> None:
    assert slugify_identifier("httpbin-org") == "httpbin-org"
    assert slugify_identifier(slugify_identifier("httpbin.org")) == "httpbin-org"
