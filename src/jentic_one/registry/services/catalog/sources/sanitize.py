"""Sanitising of connector-supplied text before it is stored.

Connector text (titles, tags, links) reaches every catalog view, MCP tool and
index, so the sync apply cleans and caps it once and stores only the result.

``clean_text`` is the rule for display text, applied in this order:

1. Unicode NFKC normalisation;
2. C0/C1 control characters, zero-width and bidi-override characters
   (U+200B to U+200F, U+202A to U+202E, U+2066 to U+2069, U+FEFF) removed; line breaks
   and tabs count as whitespace;
3. markdown images and links reduced to their text, HTML tags and backticks
   removed;
4. any remaining ``scheme://…`` URL replaced by ``[link]``;
5. runs of whitespace collapsed to one space and the ends trimmed;
6. capped at the field's length on a character boundary, ending in ``…`` when
   cut.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlparse

TITLE_MAX_LENGTH = 120
VERSION_MAX_LENGTH = 100
TAG_MAX_LENGTH = 32
TAGS_MAX_COUNT = 16
HUMAN_URL_MAX_LENGTH = 2048
HINT_MAX_LENGTH = 512

ELLIPSIS = "…"

_SPACING_CONTROLS_RE = re.compile(r"[\t\n\v\f\r\x85]")
_INVISIBLE_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^<>]*>")
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
_WHITESPACE_RE = re.compile(r"\s+")


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + ELLIPSIS


def clean_text(value: str | None, *, max_length: int) -> str | None:
    """Display text under the full sanitising rule; ``None`` when nothing is left."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value)
    text = _SPACING_CONTROLS_RE.sub(" ", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _MD_IMAGE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("`", "")
    text = _URL_RE.sub("[link]", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return None
    return _truncate(text, max_length)


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


def clean_token(value: str | None, *, max_length: int) -> str | None:
    """An opaque change token: kept verbatim when it is printable and fits, else ``None``.

    A token is compared, never shown, so it is never rewritten or cut.
    """
    if value is None or not value or len(value) > max_length:
        return None
    if _INVISIBLE_RE.search(value) or _SPACING_CONTROLS_RE.search(value):
        return None
    return value


def clean_human_url(value: str | None) -> str | None:
    """An ``https`` URL with a host, at most 2048 characters; anything else is ``None``."""
    if not value:
        return None
    candidate = value.strip()
    if len(candidate) > HUMAN_URL_MAX_LENGTH or _INVISIBLE_RE.search(candidate):
        return None
    if any(ch.isspace() for ch in candidate):
        return None
    try:
        parsed = urlparse(candidate)
    except ValueError:  # e.g. an unclosed IPv6 bracket
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return candidate


def clean_hint(value: str | None) -> str | None:
    """The stored raw hint: kept verbatim when it fits, else dropped."""
    if value is None or len(value) > HINT_MAX_LENGTH:
        return None
    return value
