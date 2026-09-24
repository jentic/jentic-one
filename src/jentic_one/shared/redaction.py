"""Central secret redaction.

A single, app-wide guard so secrets/tokens/decrypted credentials never reach
logs, traces, or error messages. The redaction is *defence in depth*: callers
should still avoid logging credentials, but if one slips into a log call's
key/value pair, the structlog processor here scrubs it before any renderer
(stdout or the JSON file sink) ever serialises it.

Two complementary strategies:

* **Key-based** — any mapping key whose lowercased name *contains* one of
  :data:`SENSITIVE_KEY_SUBSTRINGS` has its value replaced wholesale with
  :data:`REDACTED`. This catches structured fields (``authorization``,
  ``api_key``, ``set-cookie``, ``client_secret`` …) regardless of their value
  shape.
* **Value-based** — free-text strings are scanned for embedded auth material
  (``Bearer <token>``, ``Basic <b64>``) so a credential that lands in an
  otherwise-innocuous field (an error message, a URL) is still masked.

Both are applied recursively across nested dicts / lists / tuples so the whole
``event_dict`` is covered, not just its top level.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any, Final

REDACTED: Final = "***REDACTED***"

# Lowercased substrings; a mapping key matches if it *contains* any of these.
# Substring (not exact) matching deliberately catches variants like
# ``x-api-key``, ``client_secret``, ``db_password``, ``refresh_token`` without
# enumerating every spelling.
#
# Note: ``_token`` / ``-token`` (not bare ``token``) to avoid blanking non-secret
# metadata keys like ``token_type``, ``token_count``, ``token_type_hint``.
SENSITIVE_KEY_SUBSTRINGS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "auth_token",
        "_token",
        "-token",
        "secret",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "api-key",
        "cookie",
        "credential",
        "private_key",
        "encrypted_secret",
        "encrypted_password",
        "session_key",
    }
)

# Keys that are sensitive only as an exact (case-insensitive) match.
# Bare "token" is sensitive (it IS the credential), but as a substring it
# over-matches (token_type, token_count).
_SENSITIVE_EXACT_KEYS: Final[frozenset[str]] = frozenset({"token"})

# Inline auth material embedded in free-text values. The scheme/label is kept so
# the log still reads sensibly ("Bearer ***REDACTED***"); only the secret part
# is masked.
_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)\b(Basic)\s+[A-Za-z0-9+/]+=*"),
)


_MAX_REDACT_DEPTH: Final[int] = 32


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(sub in lowered for sub in SENSITIVE_KEY_SUBSTRINGS)


def _redact_str(value: str) -> str:
    """Mask any embedded auth material in a free-text string."""
    redacted = value
    for pattern in _VALUE_PATTERNS:
        redacted = pattern.sub(rf"\1 {REDACTED}", redacted)
    return redacted


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact ``value``, masking sensitive-keyed entries and auth material.

    Containers are rebuilt rather than mutated in place, so the caller's
    original object is left untouched (important when the same structure is
    also returned to a client or persisted).
    """
    if _depth >= _MAX_REDACT_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return {key: _redact_member(key, member, _depth) for key, member in value.items()}
    if isinstance(value, list):
        return [redact_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, _depth=_depth + 1) for item in value)
    if isinstance(value, str):
        return _redact_str(value)
    return value


def _redact_member(key: Any, value: Any, _depth: int = 0) -> Any:
    """Redact a single mapping entry: mask the whole value if its key is sensitive."""
    if isinstance(key, str) and _is_sensitive_key(key):
        return REDACTED
    return redact_value(value, _depth=_depth + 1)


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Public helper: redact a mapping (e.g. a headers dict) for safe logging."""
    return {key: _redact_member(key, value) for key, value in data.items()}


def redact_event(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact secrets from every log event before rendering.

    Wired into the ``ProcessorFormatter`` chain (after ``remove_processors_meta``,
    before the renderer) for both the stdout and JSON-file sinks. Every record —
    whether emitted via structlog or routed through stdlib ``logging`` — passes
    through the formatter, so this is the single app-wide redaction chokepoint.
    """
    return {key: _redact_member(key, value) for key, value in event_dict.items()}
