"""Unit tests for the pure ``Jentic-Revision`` pin parser (§10).

The parser runs at the web edge — every value is validated against the spec
regex *before* any registry lookup, so a malformed pin is a clean 422
(``InvalidRevisionPinError``) and never an uncaught ``ValueError``/``500``
downstream.
"""

from __future__ import annotations

import pytest

from jentic_one.broker.core.exceptions import InvalidRevisionPinError
from jentic_one.broker.core.revisions import parse_revisions


def test_none_header_yields_empty_map() -> None:
    assert parse_revisions(None) == {}


def test_empty_header_yields_empty_map() -> None:
    assert parse_revisions("") == {}
    assert parse_revisions("   ") == {}


def test_single_pin_parses() -> None:
    result = parse_revisions("stripe:payments:2023-10-16=rev_01HMY1Q0AB")
    assert result == {("stripe", "payments", "2023-10-16"): "rev_01HMY1Q0AB"}


def test_multi_pin_comma_separated_parses() -> None:
    header = "stripe:payments:2023-10-16=rev_01HMY1Q0AB, shopify:admin:2024-01=rev_01HMY7B3QN"
    result = parse_revisions(header)
    assert result == {
        ("stripe", "payments", "2023-10-16"): "rev_01HMY1Q0AB",
        ("shopify", "admin", "2024-01"): "rev_01HMY7B3QN",
    }


def test_surrounding_and_trailing_whitespace_tolerated() -> None:
    header = "  stripe:payments:v1=rev_abc ,  acme:foo:v2=rev_def ,"
    result = parse_revisions(header)
    assert result == {
        ("stripe", "payments", "v1"): "rev_abc",
        ("acme", "foo", "v2"): "rev_def",
    }


def test_colon_in_version_segment_is_preserved() -> None:
    # The spec's version segment (``[^=]+``) permits colons, so a value like a
    # date-ish or build-tagged version must round-trip into the version without
    # raising — the parser splits the api segment at most twice.
    result = parse_revisions("stripe:payments:v1:beta=rev_abc")
    assert result == {("stripe", "payments", "v1:beta"): "rev_abc"}


@pytest.mark.parametrize(
    "revision_id",
    [
        "3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c",
        "3F2B8C1E-7D4A-4E9B-A6C5-1D2E3F4A5B6C",
    ],
)
def test_revision_id_uuid_normalises_to_its_rev_label(revision_id: str) -> None:
    # Pins #1720: the ``revision_id`` every public response returns is a valid
    # pin as-is, and it reaches the resolver as the same ``rev_<hex>`` label.
    result = parse_revisions(f"stripe:payments:v1={revision_id}")
    assert result == {("stripe", "payments", "v1"): "rev_3f2b8c1e7d4a4e9ba6c51d2e3f4a5b6c"}


def test_uuid_and_label_pins_mix_in_one_header() -> None:
    header = (
        "stripe:payments:v1=3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c, "
        "acme:foo:v2=rev_8a1d4c2e5b6f4a7e9c3d2e1f0a9b8c7d"
    )
    assert parse_revisions(header) == {
        ("stripe", "payments", "v1"): "rev_3f2b8c1e7d4a4e9ba6c51d2e3f4a5b6c",
        ("acme", "foo", "v2"): "rev_8a1d4c2e5b6f4a7e9c3d2e1f0a9b8c7d",
    }


def test_malformed_value_detail_names_the_expected_form() -> None:
    with pytest.raises(InvalidRevisionPinError) as excinfo:
        parse_revisions("stripe:payments:v1=abc")
    assert "vendor:name:version=<revision_id>" in str(excinfo.value.detail)


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "stripe:payments:v1",  # missing =rev_…
        "stripe:payments:v1=abc",  # neither a rev_ label nor a UUID
        "stripe:payments=rev_abc",  # too few colons (no version segment)
        "STRIPE:payments:v1=rev_abc",  # uppercase vendor disallowed by regex
        "stripe:payments:v1=rev_",  # empty rev label
        "stripe:payments:v1=rev_abc!",  # non-alnum in rev label
        "stripe:payments:v1=3f2b8c1e7d4a4e9ba6c51d2e3f4a5b6c",  # bare hex, no rev_
        "stripe:payments:v1=3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6",  # UUID one digit short
        "stripe:payments:v1={3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c}",  # braced UUID
        "stripe:payments:v1=rev_3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c",  # rev_ + hyphens
    ],
)
def test_malformed_value_raises_invalid_revision_pin(bad: str) -> None:
    with pytest.raises(InvalidRevisionPinError):
        parse_revisions(bad)


def test_one_bad_value_in_a_list_rejects_the_whole_header() -> None:
    # The bad value is rejected before any (vendor,name,version) is resolved —
    # the parser raises on the first malformed entry, never returning a partial map.
    header = "stripe:payments:v1=rev_abc, garbage"
    with pytest.raises(InvalidRevisionPinError):
        parse_revisions(header)


def test_later_pin_for_same_api_wins() -> None:
    header = "stripe:payments:v1=rev_first, stripe:payments:v1=rev_second"
    result = parse_revisions(header)
    assert result == {("stripe", "payments", "v1"): "rev_second"}
