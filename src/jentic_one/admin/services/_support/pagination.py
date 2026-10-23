"""Compatibility re-export of the shared pagination helpers for admin listings.

OSS code imports :mod:`jentic_one.shared.pagination` directly. This module is a
live compatibility surface for out-of-tree callers that still import the admin
path; its ``decode_cursor`` takes a cursor alone and validates the id as a
string (admin primary keys are KSUIDs).
"""

from __future__ import annotations

from datetime import datetime

from jentic_one.shared.pagination import Page, encode_cursor
from jentic_one.shared.pagination import decode_cursor as _decode_cursor

__all__ = ["Page", "decode_cursor", "encode_cursor"]


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode an admin listing cursor into ``(timestamp, id)`` (string id check)."""
    return _decode_cursor(cursor, id_kind="str")
