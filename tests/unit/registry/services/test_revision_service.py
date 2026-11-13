"""Unit tests for revision service cursor and validation logic."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jentic_one.registry.services.revision_service import _should_resolve_catalog_update_available
from jentic_one.shared.models import ORIGIN_CATALOG, ORIGIN_OVERLAY, EventType
from jentic_one.shared.pagination import Cursor, InvalidCursorError, decode_cursor, encode_cursor


def test_encode_decode_cursor_roundtrip() -> None:
    ts = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
    id_str = "a1b2c3d4-0000-0000-0000-000000000001"
    encoded = encode_cursor(ts, id_str)
    assert decode_cursor(encoded, id_kind="uuid") == Cursor(timestamp=ts, id=id_str)


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
    assert decode_cursor(encoded, id_kind="uuid").timestamp.tzinfo == UTC


def _check_row(
    *,
    spec_url: str = "https://example.com/openapi.json",
    notified_digest: str | None = "sha256:notified",
    notified_class: str | None = EventType.CATALOG_UPDATE_AVAILABLE,
) -> SimpleNamespace:
    return SimpleNamespace(
        spec_url=spec_url,
        last_notified_digest=notified_digest,
        last_notified_event_class=notified_class,
    )


def test_should_resolve_catalog_update_available_matches_catalog_draft() -> None:
    """Pins #1490: promoting a catalog draft resolves a matching update-available check."""
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:promoted",
            source_url="https://example.com/openapi.json",
            check=_check_row(),
        )
        is True
    )


def test_should_resolve_catalog_update_available_matches_synced_baseline() -> None:
    """Pins #1490: a check row synced without an event class also advances on catalog promote."""
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:promoted",
            source_url=None,
            check=_check_row(notified_digest="sha256:baseline", notified_class=None),
        )
        is True
    )


@pytest.mark.parametrize("origin", [None, ORIGIN_OVERLAY, "manual"])
def test_should_resolve_catalog_update_available_ignores_non_catalog_origin(
    origin: str | None,
) -> None:
    """Pins #1490: manual and overlay promotions never resolve catalog update checks."""
    assert (
        _should_resolve_catalog_update_available(
            origin=origin,
            spec_digest="sha256:promoted",
            source_url="https://example.com/openapi.json",
            check=_check_row(),
        )
        is False
    )


def test_should_resolve_catalog_update_available_leaves_overlay_conflict_open() -> None:
    """Pins #1490: overlay conflict checks stay open when a draft is promoted."""
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:promoted",
            source_url="https://example.com/openapi.json",
            check=_check_row(notified_class=EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY),
        )
        is False
    )


def test_should_resolve_catalog_update_available_skips_noop_and_mismatched_source() -> None:
    """Pins #1490: missing/unnotified/already-matching checks and other URLs do not resolve."""
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:promoted",
            source_url="https://other.example.com/openapi.json",
            check=_check_row(),
        )
        is False
    )
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:notified",
            source_url="https://example.com/openapi.json",
            check=_check_row(notified_digest="sha256:notified"),
        )
        is False
    )
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:promoted",
            source_url="https://example.com/openapi.json",
            check=_check_row(notified_digest=None, notified_class=None),
        )
        is False
    )
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest="sha256:promoted",
            source_url="https://example.com/openapi.json",
            check=None,
        )
        is False
    )
    assert (
        _should_resolve_catalog_update_available(
            origin=ORIGIN_CATALOG,
            spec_digest=None,
            source_url="https://example.com/openapi.json",
            check=_check_row(),
        )
        is False
    )
