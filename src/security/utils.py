"""Shared payload-parsing utilities for security scanning."""

from __future__ import annotations

import json


def extract_text_from_payload(body_bytes: bytes) -> str:
    """Recursively extract string values from a JSON payload.

    Falls back to raw UTF-8 decoding for non-JSON bodies.
    """
    if not body_bytes:
        return ""

    try:
        payload = json.loads(body_bytes)
        if isinstance(payload, (dict, list)):
            str_values: list[str] = []
            _extract_strings(payload, str_values)
            return "\n".join(str_values)
        return body_bytes.decode("utf-8", errors="replace")
    except Exception:
        return body_bytes.decode("utf-8", errors="replace")


def extract_text_from_query_params(params) -> str:
    """Join all query parameter values into a single string."""
    if not params:
        return ""
    return " ".join(v for v in params.values() if v)


def _extract_strings(item, acc: list[str]) -> None:
    """Recursively collect string values from nested dicts/lists."""
    if isinstance(item, dict):
        for v in item.values():
            _extract_strings(v, acc)
    elif isinstance(item, list):
        for v in item:
            _extract_strings(v, acc)
    elif isinstance(item, str):
        acc.append(item)
