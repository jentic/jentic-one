"""Canonical normalization for API identity fields (vendor / name).

The registry slugifies ``vendor``/``name`` on import; any other layer that
persists or compares those fields must use the *same* normalization, or an
otherwise-identical identity mismatches on exact string equality and silently
default-denies. Keep this helper as the single source of truth.
"""

import re

API_FIELD_MAX_LENGTH = 100
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify_api_field(value: str) -> str:
    """Normalize an API vendor/name field to its canonical slug form.

    Lowercase, strip, replace runs of non-``[a-z0-9-]`` with a single hyphen,
    trim leading/trailing hyphens, and truncate to ``API_FIELD_MAX_LENGTH``.
    """
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:API_FIELD_MAX_LENGTH]
