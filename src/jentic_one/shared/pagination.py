"""Cursor-based pagination utilities."""

from __future__ import annotations

import json
import math
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

#: The check a ``(timestamp, id)`` cursor's id must pass (see :func:`decode_cursor`).
CursorIdKind = Literal["uuid", "str"]

__all__ = [
    "MAX_CURSOR_LENGTH",
    "Cursor",
    "CursorIdKind",
    "InvalidCursorError",
    "InvalidSearchCursorError",
    "Page",
    "decode_catalog_cursor",
    "decode_cursor",
    "decode_search_cursor",
    "encode_catalog_cursor",
    "encode_cursor",
    "encode_search_cursor",
]


#: Longest cursor any decoder accepts. Minted cursors are well under 200
#: characters; the cap rejects an oversized (e.g. deeply nested) payload before
#: it reaches the JSON parser.
MAX_CURSOR_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class Cursor:
    """A decoded keyset position: the sort timestamp and the row id that breaks its ties.

    Carried as one value rather than a bare pair so a caller cannot transpose
    the two fields, and so the keyset helpers name what they compare. The id is
    a string for both key shapes: a KSUID as stored, and a UUID already
    validated by :func:`decode_cursor`.
    """

    timestamp: datetime
    id: str


class InvalidCursorError(ValueError):
    """Raised when a pagination cursor cannot be decoded."""


class InvalidSearchCursorError(InvalidCursorError):
    """Raised when a search cursor cannot be decoded."""


class Page(BaseModel, Generic[T]):  # noqa: UP046
    """Paginated response envelope."""

    data: list[T]
    has_more: bool
    next_cursor: str | None = None


def encode_cursor(timestamp: datetime, id: str) -> str:
    """Base64-encode a cursor from a timestamp and ID."""
    payload = {"t": timestamp.isoformat(), "id": id}
    return b64encode(json.dumps(payload).encode()).decode()


def _load_cursor_payload(cursor: str) -> dict[str, Any]:
    """Base64-decode and JSON-parse ``cursor``, requiring a JSON object.

    Any oversized, undecodable, unparsable or non-object payload (``null``,
    ``[1]``, a bare number, thousands of nested ``[``) raises
    :class:`InvalidCursorError`, so every decoder rejects a tampered cursor the
    same way and none leaks a ``TypeError`` or ``RecursionError``.
    """
    if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidCursorError("Invalid pagination cursor")
    try:
        payload = json.loads(b64decode(cursor.encode()).decode())
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc
    if not isinstance(payload, dict):
        raise InvalidCursorError("Invalid pagination cursor")
    return payload


def decode_cursor(cursor: str, *, id_kind: CursorIdKind) -> Cursor:
    """Decode a cursor back into a :class:`Cursor`.

    ``id_kind`` names the check the id must pass: ``"uuid"`` for listings
    keyed by UUID primary keys, ``"str"`` for KSUID and other string keys.
    Every malformed cursor raises :class:`InvalidCursorError`, which the
    admin, auth, control and registry surfaces map to 400 ``invalid_cursor``.
    """
    payload = _load_cursor_payload(cursor)
    try:
        raw_ts = payload["t"]
        raw_id = payload["id"]
        if not isinstance(raw_ts, str) or not isinstance(raw_id, str):
            raise ValueError("cursor fields must be strings")
        ts = datetime.fromisoformat(raw_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if id_kind == "uuid":
            uuid.UUID(raw_id)
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc
    return Cursor(timestamp=ts, id=raw_id)


def encode_search_cursor(distance: float, operation_id: str) -> str:
    """Encode a keyset cursor from distance and operation ID."""
    payload = json.dumps({"d": distance, "id": operation_id})
    return b64encode(payload.encode()).decode()


def decode_search_cursor(cursor: str) -> tuple[float, str]:
    """Decode a search cursor back into (distance, operation_id)."""
    try:
        payload = _load_cursor_payload(cursor)
        distance = payload["d"]
        operation_id = payload["id"]
        if isinstance(distance, bool) or not isinstance(distance, int | float):
            raise ValueError("distance must be a number")
        if not isinstance(operation_id, str):
            raise ValueError("id must be a string")
        result = float(distance)
        if not math.isfinite(result):
            raise ValueError("distance must be finite")
    except (InvalidCursorError, ValueError, TypeError, KeyError) as exc:
        raise InvalidSearchCursorError("Invalid search cursor") from exc
    return result, operation_id


def encode_catalog_cursor(api_id: str, score: float | None = None) -> str:
    """Encode an in-memory catalog cursor as an opaque keyset token.

    The catalog is an in-memory list paged purely by sort key, so the cursor
    just carries the last item's position: its ``api_id`` for the browse order
    (sorted by ``api_id``) and additionally its relevance ``score`` for the
    search order (sorted by ``(-score, api_id)``).
    """
    payload: dict[str, object] = {"id": api_id}
    if score is not None:
        payload["s"] = score
    return b64encode(json.dumps(payload).encode()).decode()


def decode_catalog_cursor(cursor: str) -> tuple[str, float | None]:
    """Decode a catalog cursor into ``(api_id, score)`` (``score`` None for browse)."""
    payload = _load_cursor_payload(cursor)
    try:
        api_id = payload["id"]
        if not isinstance(api_id, str):
            raise ValueError("id must be a string")
        raw_score = payload.get("s")
        score: float | None = None
        if raw_score is not None:
            if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
                raise ValueError("score must be a number")
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError("score must be finite")
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc
    return api_id, score
