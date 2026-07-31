"""Cursor-based pagination utilities."""

from __future__ import annotations

import json
import math
import uuid
from base64 import b64decode, b64encode
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

__all__ = [
    "InvalidCursorError",
    "InvalidSearchCursorError",
    "Page",
    "decode_catalog_cursor",
    "decode_cursor",
    "decode_cursor_str",
    "decode_search_cursor",
    "encode_catalog_cursor",
    "encode_cursor",
    "encode_search_cursor",
]


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


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode a cursor back into (created_at, id) with UUID validation."""
    try:
        payload = json.loads(b64decode(cursor.encode()).decode())
        ts = datetime.fromisoformat(payload["t"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        uuid.UUID(payload["id"])
        return ts, payload["id"]
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc


def decode_cursor_str(cursor: str) -> tuple[datetime, str]:
    """Decode a cursor back into (created_at, id) without UUID validation."""
    try:
        payload = json.loads(b64decode(cursor.encode()).decode())
        ts = datetime.fromisoformat(payload["t"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if not isinstance(payload["id"], str):
            raise ValueError("id must be a string")
        return ts, payload["id"]
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc


def encode_search_cursor(distance: float, operation_id: str) -> str:
    """Encode a keyset cursor from distance and operation ID."""
    payload = json.dumps({"d": distance, "id": operation_id})
    return b64encode(payload.encode()).decode()


def decode_search_cursor(cursor: str) -> tuple[float, str]:
    """Decode a search cursor back into (distance, operation_id)."""
    try:
        payload = json.loads(b64decode(cursor.encode()).decode())
        return float(payload["d"]), str(payload["id"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidSearchCursorError("Invalid search cursor") from exc


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
    try:
        payload = json.loads(b64decode(cursor.encode()).decode())
        api_id = payload["id"]
        if not isinstance(api_id, str):
            raise ValueError("id must be a string")
        raw_score = payload.get("s")
        score = None if raw_score is None else float(raw_score)
        if score is not None and not math.isfinite(score):
            raise ValueError("score must be finite")
        return api_id, score
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc
