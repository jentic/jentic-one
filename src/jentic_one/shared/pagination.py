"""Cursor-based pagination utilities."""

from __future__ import annotations

import json
import math
import re
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

__all__ = [
    "CATALOG_STRATEGY_NAME_RE",
    "MAX_CATALOG_CURSOR_LENGTH",
    "CatalogCursor",
    "InvalidCursorError",
    "InvalidSearchCursorError",
    "Page",
    "decode_cursor",
    "decode_cursor_str",
    "decode_search_cursor",
    "encode_catalog_cursor",
    "encode_cursor",
    "encode_search_cursor",
    "parse_catalog_cursor",
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


def encode_catalog_cursor(
    api_id: str, score: float | None = None, *, strategy: str | None = None
) -> str:
    """Encode an in-memory catalog cursor as an opaque keyset token.

    The catalog is an in-memory list paged purely by sort key, so the cursor
    just carries the last item's position: its ``api_id`` for the browse order
    (sorted by ``api_id``) and additionally its relevance ``score`` for the
    search order (sorted by ``(-score, api_id)``). ``score`` is written with
    its own JSON type — an ``int`` stays an integer, and a ``float`` uses
    Python's shortest round-trip repr — so the decoded value compares exactly
    against the ranker's scores on the next page.

    ``strategy`` names the catalog search strategy that served the walk. It is
    only written for non-default strategies (key ``st``), so token and browse
    cursors keep their ``{"id"[, "s"]}`` bytes.
    """
    payload: dict[str, object] = {"id": api_id}
    if score is not None:
        payload["s"] = score
    if strategy is not None:
        payload["st"] = strategy
    return b64encode(json.dumps(payload).encode()).decode()


@dataclass(frozen=True)
class CatalogCursor:
    """A decoded catalog cursor: keyset position plus the walk's strategy (if any)."""

    api_id: str
    #: ``None`` for a browse cursor. Integers stay ``int`` so large integer
    #: scores compare exactly; anything else is a finite ``float``.
    score: int | float | None
    #: The search strategy that served the walk; ``None`` means the default.
    strategy: str | None = None


#: Catalog search strategy names: written into cursors (``st``), metric labels
#: and logs, so they are short, plain slugs.
CATALOG_STRATEGY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: Upper bound on an encoded catalog cursor. Real cursors are well under
#: 200 characters; the bound keeps a crafted cursor from reaching the JSON
#: decoder at a size that could exhaust its recursion limit.
MAX_CATALOG_CURSOR_LENGTH = 1024


def parse_catalog_cursor(cursor: str) -> CatalogCursor:
    """Decode a catalog cursor, including its optional ``st`` strategy key.

    Raises :class:`InvalidCursorError` for anything that is not a cursor this
    service could have issued: over-long input, a non-string ``id``, a score
    that is not a JSON integer or finite number (strings and booleans are
    rejected), or an ``st`` that is not a valid strategy name. Whether ``st``
    names a strategy that may serve this walk is the caller's decision.
    """
    if len(cursor) > MAX_CATALOG_CURSOR_LENGTH:
        raise InvalidCursorError("Invalid pagination cursor")
    try:
        payload = json.loads(b64decode(cursor.encode()).decode())
        api_id = payload["id"]
        if not isinstance(api_id, str):
            raise ValueError("id must be a string")
        raw_score = payload.get("s")
        score: int | float | None
        if raw_score is None:
            score = None
        elif isinstance(raw_score, bool):
            raise ValueError("score must be a number")
        elif isinstance(raw_score, int) or (
            isinstance(raw_score, float) and math.isfinite(raw_score)
        ):
            score = raw_score
        else:
            raise ValueError("score must be an integer or a finite number")
        strategy = payload.get("st")
        if strategy is not None and (
            not isinstance(strategy, str) or not CATALOG_STRATEGY_NAME_RE.fullmatch(strategy)
        ):
            raise ValueError("st must be a strategy name")
        return CatalogCursor(api_id=api_id, score=score, strategy=strategy)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        json.JSONDecodeError,
    ) as exc:
        raise InvalidCursorError("Invalid pagination cursor") from exc
