"""Sanitising of connector-supplied text before it is stored.

Connector text (titles, tags, links) reaches every catalog view, MCP tool and
index, so the sync apply cleans and caps it once and stores only the result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

TITLE_MAX_LENGTH = 120
VERSION_MAX_LENGTH = 100
TAG_MAX_LENGTH = 32
TAGS_MAX_COUNT = 16
HUMAN_URL_MAX_LENGTH = 2048
HINT_MAX_LENGTH = 512

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(value: str | None, *, max_length: int) -> str | None:
    """Drop control and bidi characters, collapse whitespace, cap; ``None`` if empty."""
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", _CONTROL_RE.sub(" ", value)).strip()
    if not cleaned:
        return None
    return cleaned[:max_length].rstrip()


def clean_tags(tags: Iterable[str]) -> list[str]:
    """Cleaned, de-duplicated tags: at most 16, each at most 32 characters."""
    out: list[str] = []
    for tag in tags:
        cleaned = clean_text(tag, max_length=TAG_MAX_LENGTH) if isinstance(tag, str) else None
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= TAGS_MAX_COUNT:
            break
    return out


def clean_human_url(value: str | None) -> str | None:
    """An ``https`` URL with a host, at most 2048 characters; anything else is ``None``."""
    if not value:
        return None
    candidate = value.strip()
    if len(candidate) > HUMAN_URL_MAX_LENGTH or _CONTROL_RE.search(candidate):
        return None
    if any(ch.isspace() for ch in candidate):
        return None
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return candidate


def clean_hint(value: str | None) -> str | None:
    """The stored raw hint: kept verbatim when it fits, else dropped."""
    if value is None or len(value) > HINT_MAX_LENGTH:
        return None
    return value
