"""Property-based tests for shared cursor pagination encoders and decoders."""

from __future__ import annotations

import contextlib
import json
import uuid
from base64 import b64encode
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from jentic_one.shared.pagination import (
    Cursor,
    InvalidCursorError,
    InvalidSearchCursorError,
    decode_catalog_cursor,
    decode_cursor,
    decode_search_cursor,
    encode_catalog_cursor,
    encode_cursor,
    encode_search_cursor,
)

_DECODERS: tuple[Callable[[str], object], ...] = (
    lambda c: decode_cursor(c, id_kind="uuid"),
    lambda c: decode_cursor(c, id_kind="str"),
    decode_search_cursor,
    decode_catalog_cursor,
)

_JSON_SCALAR = st.none() | st.booleans() | st.integers() | st.floats() | st.text()
_JSON_VALUE: st.SearchStrategy[Any] = st.recursive(
    _JSON_SCALAR,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(), children, max_size=5)
    ),
    max_leaves=15,
)

_CURSOR_INPUT = st.one_of(
    st.text(),
    _JSON_VALUE.map(lambda value: b64encode(json.dumps(value).encode()).decode()),
)

_FINITE_FLOATS = st.floats(allow_nan=False, allow_infinity=False)

_UUID_FORMATTERS: tuple[Callable[[uuid.UUID], str], ...] = (
    str,
    lambda u: f"urn:uuid:{u}",
    lambda u: "{" + u.hex + "}",
)


@given(raw=_CURSOR_INPUT)
def test_decoders_raise_only_invalid_cursor(raw: str) -> None:
    """Every pagination decoder either decodes valid input or raises InvalidCursorError."""
    for decode in _DECODERS:
        with contextlib.suppress(InvalidCursorError):
            decode(raw)

    with contextlib.suppress(InvalidSearchCursorError):
        decode_search_cursor(raw)


@given(
    ts=st.datetimes(timezones=st.just(UTC)),
    row_id=st.text(max_size=50),
)
def test_cursor_round_trip(ts: datetime, row_id: str) -> None:
    """Timestamp cursors round-trip losslessly for arbitrary UTC datetimes and string IDs."""
    cursor = encode_cursor(ts, row_id)
    assert decode_cursor(cursor, id_kind="str") == Cursor(timestamp=ts, id=row_id)


@given(
    score=_FINITE_FLOATS,
    item_id=st.text(max_size=50),
)
def test_search_and_catalog_cursor_round_trip(score: float, item_id: str) -> None:
    """Search and catalog cursors round-trip finite floats and arbitrary string IDs."""
    search_cursor = encode_search_cursor(score, item_id)
    assert decode_search_cursor(search_cursor) == (score, item_id)

    browse_cursor = encode_catalog_cursor(item_id)
    assert decode_catalog_cursor(browse_cursor) == (item_id, None)

    scored_catalog_cursor = encode_catalog_cursor(item_id, score)
    assert decode_catalog_cursor(scored_catalog_cursor) == (item_id, score)


@given(
    u=st.uuids(),
    fmt=st.sampled_from(_UUID_FORMATTERS),
)
def test_uuid_cursor_id_is_canonical(
    u: uuid.UUID,
    fmt: Callable[[uuid.UUID], str],
) -> None:
    """UUID cursors always decode to the canonical hyphenated lowercase UUID string."""
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    cursor = encode_cursor(ts, fmt(u))
    decoded = decode_cursor(cursor, id_kind="uuid")
    assert decoded.id == str(u)
