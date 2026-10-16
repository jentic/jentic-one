"""Unit tests for the pure ``Jentic-Revision`` pin parser (§10).

The parser runs at the web edge — every value is validated against the spec
regex *before* any registry lookup, so a malformed pin is a clean 422
(``InvalidRevisionPinError``) and never an uncaught ``ValueError``/``500``
downstream.
"""

from __future__ import annotations

import uuid

import pytest

from jentic_one.broker.core.exceptions import InvalidRevisionPinError
from jentic_one.broker.core.revisions import parse_revisions, rev_label
from jentic_one.shared.broker.revision_label import decode_rev_label, encode_rev_label

REVISION_ID = "3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c"
LABEL = "rev_3f2b8c1e7d4a4e9ba6c51d2e3f4a5b6c"
OTHER_LABEL = "rev_8a1d4c2e5b6f4a7e9c3d2e1f0a9b8c7d"


def test_none_header_yields_empty_map() -> None:
    assert parse_revisions(None) == {}


def test_empty_header_yields_empty_map() -> None:
    assert parse_revisions("") == {}
    assert parse_revisions("   ") == {}


def test_single_pin_parses() -> None:
    result = parse_revisions(f"stripe:payments:2023-10-16={LABEL}")
    assert result == {("stripe", "payments", "2023-10-16"): LABEL}


def test_multi_pin_comma_separated_parses() -> None:
    header = f"stripe:payments:2023-10-16={LABEL}, shopify:admin:2024-01={OTHER_LABEL}"
    result = parse_revisions(header)
    assert result == {
        ("stripe", "payments", "2023-10-16"): LABEL,
        ("shopify", "admin", "2024-01"): OTHER_LABEL,
    }


def test_surrounding_and_trailing_whitespace_tolerated() -> None:
    header = f"  stripe:payments:v1={LABEL} ,  acme:foo:v2={OTHER_LABEL} ,"
    result = parse_revisions(header)
    assert result == {
        ("stripe", "payments", "v1"): LABEL,
        ("acme", "foo", "v2"): OTHER_LABEL,
    }


def test_colon_in_version_segment_is_preserved() -> None:
    # The spec's version segment (``[^=]+``) permits colons, so a value like a
    # date-ish or build-tagged version must round-trip into the version without
    # raising — the parser splits the api segment at most twice.
    result = parse_revisions(f"stripe:payments:v1:beta={LABEL}")
    assert result == {("stripe", "payments", "v1:beta"): LABEL}


@pytest.mark.parametrize("revision_id", [REVISION_ID, REVISION_ID.upper()])
def test_revision_id_uuid_is_kept_as_sent_and_maps_to_its_rev_label(revision_id: str) -> None:
    # Pins #1720: the ``revision_id`` every public response returns is a valid
    # pin as-is. The map keeps the caller's spelling (so errors can echo it) and
    # ``rev_label`` gives the resolver the same label as the ``rev_`` form.
    result = parse_revisions(f"stripe:payments:v1={revision_id}")
    assert result == {("stripe", "payments", "v1"): revision_id}
    assert rev_label(revision_id) == LABEL


def test_rev_label_lowercases_an_uppercase_label() -> None:
    assert rev_label(LABEL.upper().replace("REV_", "rev_")) == LABEL
    assert rev_label(LABEL) == LABEL


def test_rev_label_encoding_round_trips() -> None:
    revision_id = uuid.UUID(REVISION_ID)
    assert encode_rev_label(revision_id) == LABEL
    assert decode_rev_label(LABEL) == revision_id
    assert decode_rev_label(REVISION_ID) is None
    assert decode_rev_label("rev_notahexvalue") is None


def test_uuid_and_label_pins_mix_in_one_header() -> None:
    header = f"stripe:payments:v1={REVISION_ID}, acme:foo:v2={OTHER_LABEL}"
    assert parse_revisions(header) == {
        ("stripe", "payments", "v1"): REVISION_ID,
        ("acme", "foo", "v2"): OTHER_LABEL,
    }


def test_malformed_value_detail_names_the_expected_form() -> None:
    with pytest.raises(InvalidRevisionPinError) as excinfo:
        parse_revisions("stripe:payments:v1=abc")
    assert "vendor:name:version=<revision_id>" in str(excinfo.value.detail)


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "stripe:payments:v1",  # missing =<revision>
        "stripe:payments:v1=abc",  # neither a rev_ label nor a UUID
        f"stripe:payments={LABEL}",  # too few colons (no version segment)
        f"STRIPE:payments:v1={LABEL}",  # uppercase vendor disallowed by regex
        "stripe:payments:v1=rev_",  # empty rev label
        f"stripe:payments:v1={LABEL}!",  # trailing non-hex in rev label
        "stripe:payments:v1=rev_abc",  # rev_ label shorter than a uuid.hex
        "stripe:payments:v1=rev_01HMY1Q0AB01HMY1Q0AB01HMY1Q0ABCD",  # 32 chars, not hex
        f"stripe:payments:v1={LABEL}0",  # rev_ label longer than a uuid.hex
        "stripe:payments:v1=3f2b8c1e7d4a4e9ba6c51d2e3f4a5b6c",  # bare hex, no rev_
        "stripe:payments:v1=3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6",  # UUID one digit short
        "stripe:payments:v1={3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c}",  # braced UUID
        "stripe:payments:v1=rev_3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c",  # rev_ + hyphens
    ],
)
def test_malformed_value_raises_invalid_revision_pin(bad: str) -> None:
    with pytest.raises(InvalidRevisionPinError) as excinfo:
        parse_revisions(bad)
    assert excinfo.value.type == "invalid_revision_pin"


def test_one_bad_value_in_a_list_rejects_the_whole_header() -> None:
    # The bad value is rejected before any (vendor,name,version) is resolved —
    # the parser raises on the first malformed entry, never returning a partial map.
    header = f"stripe:payments:v1={LABEL}, garbage"
    with pytest.raises(InvalidRevisionPinError):
        parse_revisions(header)


@pytest.mark.parametrize(
    "header",
    [
        f"stripe:payments:v1={LABEL}, stripe:payments:v1={LABEL}",
        f"stripe:payments:v1={REVISION_ID}, stripe:payments:v1={LABEL}",
        f"stripe:payments:v1={LABEL}, stripe:payments:v1={REVISION_ID.upper()}",
    ],
)
def test_repeated_pin_to_the_same_revision_is_accepted(header: str) -> None:
    # Either spelling of one revision names the same pin; the last spelling is kept.
    result = parse_revisions(header)
    assert list(result) == [("stripe", "payments", "v1")]
    assert rev_label(result[("stripe", "payments", "v1")]) == LABEL


def test_conflicting_pins_for_one_api_are_rejected() -> None:
    # Two different revisions for one API are ambiguous: silently keeping either
    # would route the call through a revision the caller may not have meant.
    header = f"stripe:payments:v1={REVISION_ID}, stripe:payments:v1={OTHER_LABEL}"
    with pytest.raises(InvalidRevisionPinError) as excinfo:
        parse_revisions(header)
    assert excinfo.value.type == "invalid_revision_pin"
    assert REVISION_ID in excinfo.value.detail
    assert OTHER_LABEL in excinfo.value.detail
