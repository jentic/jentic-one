"""Shared OAuth / DCR scope-string token validator."""

from __future__ import annotations

import re

SCOPE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_:./-]+$")


def validate_scope_string(v: str | None) -> str | None:
    """Validate a space-separated OAuth/DCR scope string."""
    if v is None:
        return v
    tokens = v.split()
    if len(tokens) > 100:
        msg = "scope must contain at most 100 tokens"
        raise ValueError(msg)
    for token in tokens:
        if len(token) > 64:
            msg = f"each scope token must be at most 64 characters, got {len(token)}"
            raise ValueError(msg)
        if not SCOPE_TOKEN_RE.match(token):
            msg = f"scope token '{token}' contains invalid characters"
            raise ValueError(msg)
    return v
