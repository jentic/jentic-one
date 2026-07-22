"""Vendor/name slug normalization shared across the registry and control planes.

The registry normalizes an API's vendor and name at import time (dots and other
non-alphanumeric characters become hyphens, e.g. ``httpbin.org`` -> ``httpbin-org``).
Credentials must store the *same* normalized form so the broker's vendor join
(``credentials.api_vendor`` against the discovered, normalized vendor) matches
instead of silently returning zero rows and default-denying. See issue #656.

This module is the single source of truth for that normalization so the two
planes cannot drift.
"""

from __future__ import annotations

import re

_MAX_FIELD_LENGTH = 100
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify_identifier(value: str) -> str:
    """Lowercase, strip, replace non-alphanumeric runs with hyphens, truncate."""
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:_MAX_FIELD_LENGTH]
