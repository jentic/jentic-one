"""The ``catalog_api_id`` grammar, shared by config validation and the resolver.

A ``catalog_api_id`` is ``domain[/sub]``: one or two ``/``-separated segments, no
leading ``/``, no empty, ``.`` or ``..`` segment, no whitespace or control
characters, at most 255 characters. Values are compared and stored verbatim:
nothing here case-folds or slugifies, because credential scopes derive from the
raw value.

``~`` is reserved for the resolver, which namespaces an id as
``{source_slug}~{hint}``. A connector hint or a mapping row containing it is
invalid, so no source can propose an id that looks namespaced by another one.
"""

from __future__ import annotations

#: Upper bound on a ``catalog_api_id`` (the ``apis.catalog_api_id`` column width).
CATALOG_API_ID_MAX_LENGTH = 255

#: Separator the resolver puts between a source slug and a namespaced hint.
NAMESPACE_SEPARATOR = "~"


def is_valid_catalog_api_id(value: str) -> bool:
    """Whether ``value`` (already stripped) matches the ``catalog_api_id`` grammar."""
    if not value or len(value) > CATALOG_API_ID_MAX_LENGTH:
        return False
    if NAMESPACE_SEPARATOR in value:
        return False
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return False
    segments = value.split("/")
    if len(segments) > 2:
        return False
    return all(segment not in ("", ".", "..") for segment in segments)


def normalise_catalog_api_id(value: str | None) -> str | None:
    """Strip surrounding whitespace and validate; ``None`` when absent or invalid."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if is_valid_catalog_api_id(stripped) else None
