"""Tests for shared cursor-based pagination utilities."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from jentic_one.shared.pagination import (
    InvalidCursorError,
    InvalidSearchCursorError,
    decode_catalog_cursor,
    decode_cursor,
    decode_cursor_str,
    decode_search_cursor,
    encode_catalog_cursor,
    encode_cursor,
    encode_search_cursor,
)


def test_encode_decode_round_trip_utc() -> None:
    ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    uid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    cursor = encode_cursor(ts, uid)
    decoded_ts, decoded_id = decode_cursor(cursor)
    assert decoded_ts == ts
    assert decoded_id == uid


def test_encode_decode_naive_datetime_gets_utc() -> None:
    ts = datetime(2024, 1, 1, 0, 0, 0)
    uid = "00000000-0000-0000-0000-000000000001"
    cursor = encode_cursor(ts, uid)
    decoded_ts, _ = decode_cursor(cursor)
    assert decoded_ts.tzinfo == UTC


def test_encode_decode_round_trip_with_explicit_utc() -> None:
    ts = datetime(2024, 3, 15, 8, 30, 0, tzinfo=UTC)
    uid = "12345678-1234-1234-1234-123456789abc"
    cursor = encode_cursor(ts, uid)
    decoded_ts, decoded_id = decode_cursor(cursor)
    assert decoded_ts == ts
    assert decoded_id == uid


def test_decode_cursor_malformed_base64() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-valid-base64!!!")


def test_decode_cursor_invalid_json() -> None:
    bad = base64.b64encode(b"not json").decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad)


def test_decode_cursor_missing_keys() -> None:
    bad = base64.b64encode(json.dumps({"x": 1}).encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad)


def test_decode_cursor_non_uuid_id() -> None:
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    cursor = encode_cursor(ts, "not-a-uuid")
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


def test_decode_cursor_str_accepts_non_uuid_id() -> None:
    ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    cursor = encode_cursor(ts, "my-string-id")
    decoded_ts, decoded_id = decode_cursor_str(cursor)
    assert decoded_ts == ts
    assert decoded_id == "my-string-id"


def test_decode_cursor_str_accepts_uuid_id() -> None:
    ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    uid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    cursor = encode_cursor(ts, uid)
    decoded_ts, decoded_id = decode_cursor_str(cursor)
    assert decoded_ts == ts
    assert decoded_id == uid


def test_decode_cursor_str_malformed_raises_error() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor_str("garbage")


def test_search_cursor_round_trip() -> None:
    distance = 0.123456
    op_id = "op-abc-123"
    cursor = encode_search_cursor(distance, op_id)
    decoded_distance, decoded_id = decode_search_cursor(cursor)
    assert decoded_distance == pytest.approx(distance)
    assert decoded_id == op_id


def test_search_cursor_malformed_raises_error() -> None:
    with pytest.raises(InvalidSearchCursorError):
        decode_search_cursor("not-a-cursor")


def test_search_cursor_error_is_subclass_of_invalid_cursor() -> None:
    with pytest.raises(InvalidCursorError):
        decode_search_cursor("not-a-cursor")


def test_catalog_cursor_browse_round_trip() -> None:
    cursor = encode_catalog_cursor("googleapis.com/admin")
    api_id, score = decode_catalog_cursor(cursor)
    assert api_id == "googleapis.com/admin"
    assert score is None


def test_catalog_cursor_search_round_trip_carries_score() -> None:
    cursor = encode_catalog_cursor("stripe.com", 0.5)
    api_id, score = decode_catalog_cursor(cursor)
    assert api_id == "stripe.com"
    assert score == pytest.approx(0.5)


def test_catalog_cursor_malformed_raises_invalid_cursor() -> None:
    with pytest.raises(InvalidCursorError):
        decode_catalog_cursor("garbage!!!")


def test_catalog_cursor_missing_id_raises_invalid_cursor() -> None:
    bad = base64.b64encode(json.dumps({"s": 0.1}).encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_catalog_cursor(bad)


def test_catalog_cursor_non_numeric_score_raises_invalid_cursor() -> None:
    # A list for "s" makes float() raise TypeError — must map to InvalidCursorError
    # (i.e. a 400 at the router), not escape as a 500.
    bad = base64.b64encode(json.dumps({"id": "x", "s": [1, 2]}).encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_catalog_cursor(bad)


@pytest.mark.parametrize("raw_score", ["NaN", "Infinity", "-Infinity", '"nan"', '"inf"', "1e400"])
def test_catalog_cursor_non_finite_score_raises_invalid_cursor(raw_score: str) -> None:
    # We only ever mint finite scores; a non-finite "s" is a crafted cursor.
    # NaN in particular compares False against everything, which would make the
    # keyset comparator's behavior depend on non-obvious fallthrough — reject at
    # the decode boundary instead (400, not a silently weird page).
    bad = base64.b64encode(f'{{"id": "x", "s": {raw_score}}}'.encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_catalog_cursor(bad)
