"""Unit tests for revision service cursor and validation logic."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from jentic_one.shared.pagination import InvalidCursorError, decode_cursor, encode_cursor


def test_encode_decode_cursor_roundtrip() -> None:
    ts = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
    id_str = "a1b2c3d4-0000-0000-0000-000000000001"
    encoded = encode_cursor(ts, id_str)
    decoded_ts, decoded_id = decode_cursor(encoded, id_kind="uuid")
    assert decoded_ts == ts
    assert decoded_id == id_str


def test_decode_cursor_invalid_base64() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-base64!!!", id_kind="uuid")


def test_decode_cursor_invalid_json() -> None:
    encoded = base64.b64encode(b"not json").decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(encoded, id_kind="uuid")


def test_decode_cursor_missing_fields() -> None:
    encoded = base64.b64encode(json.dumps({"t": "2024-01-01T00:00:00"}).encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(encoded, id_kind="uuid")


def test_decode_cursor_invalid_uuid() -> None:
    encoded = base64.b64encode(
        json.dumps({"t": "2024-01-01T00:00:00", "id": "not-a-uuid"}).encode()
    ).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(encoded, id_kind="uuid")


def test_decode_cursor_naive_timestamp_gets_utc() -> None:
    payload = {"t": "2024-01-01T00:00:00", "id": "a1b2c3d4-0000-0000-0000-000000000001"}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    ts, _ = decode_cursor(encoded, id_kind="uuid")
    assert ts.tzinfo == UTC
