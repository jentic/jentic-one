"""Unit tests for the pure catalog logo helpers (image sniffing, cache freshness)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.registry.services.catalog import logos

_NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"GIF89a\x01\x00", "image/gif"),
        (b"GIF87a\x01\x00", "image/gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
    ],
)
def test_sniff_recognises_raster_types(content: bytes, expected: str) -> None:
    assert logos.sniff_image_type(content) == expected


@pytest.mark.parametrize(
    "content",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        b'<?xml version="1.0"?><svg/>',
        b"<html><script>alert(1)</script></html>",
        b"RIFF\x24\x00\x00\x00WAVEfmt ",
        b"\x89PN",
        b"",
    ],
)
def test_sniff_rejects_everything_else(content: bytes) -> None:
    assert logos.sniff_image_type(content) is None


def test_ok_row_is_fresh_within_max_age() -> None:
    fetched = _NOW - timedelta(days=6)
    assert logos.is_fresh(logos.LOGO_STATUS_OK, fetched, now=_NOW, max_age_seconds=7 * 86400)
    assert not logos.is_fresh(
        logos.LOGO_STATUS_OK, _NOW - timedelta(days=8), now=_NOW, max_age_seconds=7 * 86400
    )


def test_unsupported_row_uses_full_max_age() -> None:
    fetched = _NOW - timedelta(days=2)
    assert logos.is_fresh(
        logos.LOGO_STATUS_UNSUPPORTED, fetched, now=_NOW, max_age_seconds=7 * 86400
    )


def test_error_row_is_retried_after_an_hour() -> None:
    within = _NOW - timedelta(seconds=logos.ERROR_RETRY_SECONDS - 60)
    past = _NOW - timedelta(seconds=logos.ERROR_RETRY_SECONDS + 60)
    assert logos.is_fresh(logos.LOGO_STATUS_ERROR, within, now=_NOW, max_age_seconds=7 * 86400)
    assert not logos.is_fresh(logos.LOGO_STATUS_ERROR, past, now=_NOW, max_age_seconds=7 * 86400)


def test_error_retry_never_exceeds_max_age() -> None:
    fetched = _NOW - timedelta(seconds=120)
    assert not logos.is_fresh(logos.LOGO_STATUS_ERROR, fetched, now=_NOW, max_age_seconds=60)
