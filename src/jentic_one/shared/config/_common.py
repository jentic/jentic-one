"""Shared helpers used across the configuration sections."""

from __future__ import annotations

from typing import Any

from jentic_one.shared.url import normalize_base_url


class ConfigError(Exception):
    """Raised when configuration is invalid or incomplete."""


def _normalize_optional_base_url(value: str | None) -> str | None:
    """Validator for optional public base-URL fields.

    Leaves ``""``/``None`` untouched (unset = "derive it"); otherwise
    normalizes (strip trailing slash, require http(s), reject userinfo) via the
    shared helper. Reused across every config field that holds a public origin
    so they can never disagree on shape.
    """
    if not value:
        return value
    return normalize_base_url(value)


def _csv_to_list(value: Any) -> list[str]:
    """Coerce a comma-separated string into a list; pass through lists unchanged."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise TypeError(f"expected list or comma-separated string, got {type(value).__name__}")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, preferring override values."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
