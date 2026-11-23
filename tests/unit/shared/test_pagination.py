"""Tests for shared cursor-based pagination utilities."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from jentic_one.shared.pagination import (
    MAX_CURSOR_LENGTH,
    Cursor,
    CursorIdKind,
    InvalidCursorError,
    InvalidSearchCursorError,
    decode_catalog_cursor,
    decode_cursor,
    decode_search_cursor,
    encode_catalog_cursor,
    encode_cursor,
    encode_search_cursor,
)


def test_encode_decode_round_trip_utc() -> None:
    ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    uid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    cursor = encode_cursor(ts, uid)
    assert decode_cursor(cursor, id_kind="uuid") == Cursor(timestamp=ts, id=uid)


def test_encode_decode_naive_datetime_gets_utc() -> None:
    ts = datetime(2024, 1, 1, 0, 0, 0)
    uid = "00000000-0000-0000-0000-000000000001"
    cursor = encode_cursor(ts, uid)
    assert decode_cursor(cursor, id_kind="uuid").timestamp.tzinfo == UTC


def test_encode_decode_round_trip_with_explicit_utc() -> None:
    ts = datetime(2024, 3, 15, 8, 30, 0, tzinfo=UTC)
    uid = "12345678-1234-1234-1234-123456789abc"
    cursor = encode_cursor(ts, uid)
    assert decode_cursor(cursor, id_kind="uuid") == Cursor(timestamp=ts, id=uid)


def test_decode_cursor_malformed_base64() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-valid-base64!!!", id_kind="uuid")


def test_decode_cursor_invalid_json() -> None:
    bad = base64.b64encode(b"not json").decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad, id_kind="uuid")


def test_decode_cursor_missing_keys() -> None:
    bad = base64.b64encode(json.dumps({"x": 1}).encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad, id_kind="uuid")


def test_decode_cursor_non_uuid_id() -> None:
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    cursor = encode_cursor(ts, "not-a-uuid")
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, id_kind="uuid")


def test_decode_cursor_str_accepts_non_uuid_id() -> None:
    ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    cursor = encode_cursor(ts, "my-string-id")
    assert decode_cursor(cursor, id_kind="str") == Cursor(timestamp=ts, id="my-string-id")


def test_decode_cursor_str_accepts_uuid_id() -> None:
    ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    uid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    cursor = encode_cursor(ts, uid)
    assert decode_cursor(cursor, id_kind="str") == Cursor(timestamp=ts, id=uid)


def test_decode_cursor_str_malformed_raises_error() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("garbage", id_kind="str")


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


def _b64(raw: str) -> str:
    return base64.b64encode(raw.encode()).decode()


#: Cursors every decoder must reject: each decodes to valid JSON of the wrong
#: shape (or to nothing usable), which is what a tampered or hand-built cursor
#: looks like. Pins #1956: these map to 400 ``invalid_cursor``, never a 500.
_MALFORMED_CURSORS = [
    pytest.param(_b64("null"), id="json-null"),
    pytest.param(_b64("[1]"), id="json-list"),
    pytest.param(_b64("1"), id="json-number"),
    pytest.param(_b64('"text"'), id="json-string"),
    pytest.param(_b64("true"), id="json-bool"),
    pytest.param(_b64('{"t": 1, "id": "x", "d": "x", "s": "x"}'), id="wrong-typed-sort-keys"),
    pytest.param(_b64('{"t": null, "id": null}'), id="null-fields"),
    pytest.param(_b64('{"t": "2024-01-01T00:00:00", "id": 1}'), id="numeric-id"),
    pytest.param(_b64('{"t": [1], "id": {"a": 1}}'), id="container-fields"),
    pytest.param(_b64('{"d": [1], "id": "x", "s": {"a": 1}}'), id="container-sort-key"),
    pytest.param(_b64('{"d": "0.5", "id": 7, "s": "high"}'), id="string-sort-key"),
    pytest.param(_b64("{}"), id="empty-object"),
    pytest.param(_b64("not json"), id="not-json"),
    pytest.param(base64.b64encode(b"\xff\xfe").decode(), id="not-utf8"),
    pytest.param("not-valid-base64!!!", id="not-base64"),
    pytest.param("", id="empty"),
    pytest.param(_b64("[" * 100_000), id="deeply-nested"),
    pytest.param(_b64('{"id": "x", "t": "' + "a" * 2000 + '"}'), id="oversized"),
]

_DECODERS: list[Callable[[str], object]] = [
    lambda c: decode_cursor(c, id_kind="uuid"),
    lambda c: decode_cursor(c, id_kind="str"),
    decode_search_cursor,
    decode_catalog_cursor,
]


@pytest.mark.parametrize("cursor", _MALFORMED_CURSORS)
@pytest.mark.parametrize(
    "decode", _DECODERS, ids=["cursor-uuid", "cursor-str", "search", "catalog"]
)
def test_every_decoder_rejects_malformed_cursor(
    decode: Callable[[str], object], cursor: str
) -> None:
    """Every decoder raises InvalidCursorError (400) for a wrong-shaped payload."""
    with pytest.raises(InvalidCursorError):
        decode(cursor)


@pytest.mark.parametrize("cursor", _MALFORMED_CURSORS)
def test_search_decoder_raises_search_specific_error(cursor: str) -> None:
    """The search decoder keeps its own subclass so search routers can tell it apart."""
    with pytest.raises(InvalidSearchCursorError):
        decode_search_cursor(cursor)


@pytest.mark.parametrize("raw", ['{"d": NaN, "id": "x"}', '{"d": true, "id": "x"}'])
def test_search_cursor_rejects_non_finite_or_boolean_distance(raw: str) -> None:
    with pytest.raises(InvalidSearchCursorError):
        decode_search_cursor(_b64(raw))


@pytest.mark.parametrize("id_kind", ["uuid", "str"])
def test_timestamp_cursor_rejects_numeric_timestamp(id_kind: CursorIdKind) -> None:
    """``{"t": 1, "id": "x"}`` is a 400, not a ``TypeError`` from ``fromisoformat``."""
    with pytest.raises(InvalidCursorError):
        decode_cursor(_b64('{"t": 1, "id": "x"}'), id_kind=id_kind)


@pytest.mark.parametrize("raw", ['{"id": "x", "s": true}', '{"id": "x", "s": "0.5"}'])
def test_catalog_cursor_rejects_boolean_or_string_score(raw: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_catalog_cursor(_b64(raw))


def test_deeply_nested_cursor_under_the_cap_is_rejected() -> None:
    """A nested payload under the length cap is still an InvalidCursorError, not a 500."""
    nested = _b64("[" * 700)
    assert len(nested) <= MAX_CURSOR_LENGTH
    for decode in _DECODERS:
        with pytest.raises(InvalidCursorError):
            decode(nested)
