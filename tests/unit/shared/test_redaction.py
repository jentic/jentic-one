"""Unit tests for central secret redaction (§08 E3.2)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
import structlog

from jentic_one.shared.config import AppConfig
from jentic_one.shared.logging import configure_logging
from jentic_one.shared.redaction import (
    REDACTED,
    redact_event,
    redact_mapping,
    redact_query_string,
    redact_url_query,
    redact_value,
)

_SECRET = "sk-supersecret-DO-NOT-LEAK-12345"  # pragma: allowlist secret


def test_sensitive_key_value_is_masked() -> None:
    out = redact_value({"authorization": f"Bearer {_SECRET}", "user": "alice"})
    assert out["authorization"] == REDACTED
    assert out["user"] == "alice"


@pytest.mark.parametrize(
    "key",
    [
        "Authorization",
        "X-API-Key",
        "api_key",
        "client_secret",
        "db_password",
        "refresh_token",
        "Set-Cookie",
        "encrypted_secret",
    ],
)
def test_sensitive_key_matching_is_case_insensitive_and_substring(key: str) -> None:
    assert redact_value({key: _SECRET})[key] == REDACTED


def test_nested_dicts_and_lists_are_redacted() -> None:
    payload = {
        "headers": {"authorization": f"Bearer {_SECRET}"},
        "items": [{"token": _SECRET}, {"name": "ok"}],
        "meta": ("plain", {"password": _SECRET}),
    }
    out = redact_value(payload)
    assert out["headers"]["authorization"] == REDACTED
    assert out["items"][0]["token"] == REDACTED
    assert out["items"][1]["name"] == "ok"
    assert out["meta"][1]["password"] == REDACTED
    assert out["meta"][0] == "plain"


def test_bearer_value_pattern_redacted_in_free_text() -> None:
    out = redact_value({"detail": f"upstream rejected token Bearer {_SECRET} for host"})
    assert _SECRET not in out["detail"]
    assert "Bearer" in out["detail"]
    assert REDACTED in out["detail"]


def test_basic_value_pattern_redacted_in_free_text() -> None:
    out = redact_value({"detail": "auth header was Basic dXNlcjpwYXNzd29yZA=="})
    assert "dXNlcjpwYXNzd29yZA==" not in out["detail"]
    assert "Basic" in out["detail"]


def test_non_sensitive_values_untouched() -> None:
    payload = {"count": 3, "ok": True, "host": "api.example.com", "ratio": 0.5}
    assert redact_value(payload) == payload


def test_original_mapping_not_mutated() -> None:
    original = {"authorization": f"Bearer {_SECRET}"}
    redact_value(original)
    assert original["authorization"] == f"Bearer {_SECRET}"


def test_redact_mapping_helper() -> None:
    assert redact_mapping({"x-api-key": _SECRET, "accept": "json"}) == {
        "x-api-key": REDACTED,
        "accept": "json",
    }


def test_redact_event_processor_shape() -> None:
    out = redact_event(None, "info", {"event": "called", "password": _SECRET})
    assert out["event"] == "called"
    assert out["password"] == REDACTED


def test_redaction_is_idempotent() -> None:
    once = redact_value({"detail": f"Bearer {_SECRET}"})
    twice = redact_value(once)
    assert once == twice


def test_token_type_not_redacted() -> None:
    """Regression: bare 'token' as substring must not blank non-secret metadata."""
    payload = {"token_type": "access", "token_count": 42, "token_type_hint": "refresh_token"}
    out = redact_value(payload)
    assert out["token_type"] == "access"
    assert out["token_count"] == 42
    assert out["token_type_hint"] == "refresh_token"


def test_bare_token_key_is_redacted() -> None:
    """A field named exactly 'token' IS the credential — it should be masked."""
    assert redact_value({"token": _SECRET})["token"] == REDACTED


def test_depth_guard_prevents_crash() -> None:
    """Deeply nested structures are safely truncated instead of raising RecursionError."""
    deep: dict[str, Any] = {"a": "leaf"}
    for _ in range(200):
        deep = {"nested": deep}
    out = redact_value(deep)
    # Should not crash; the deepest levels are replaced with REDACTED
    assert out is not None


def _logging_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "r"},
                "admin": {"name": "a"},
                "control": {"name": "c"},
            },
            "runtime": {"debug": False, "log_level": "INFO"},
        }
    )


def test_secret_never_reaches_emitted_log(capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end: a secret logged through the configured chain is masked on the sink."""
    configure_logging(_logging_config())
    try:
        log = structlog.get_logger("redaction_test")
        log.info(
            "upstream_call",
            authorization=f"Bearer {_SECRET}",
            detail=f"token Bearer {_SECRET} rejected",
            host="api.example.com",
        )
    finally:
        for handler in logging.getLogger().handlers:
            handler.flush()

    captured = capsys.readouterr().out
    assert _SECRET not in captured
    assert REDACTED in captured
    assert "api.example.com" in captured


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"https://api.example.com/v1/x?api_key={_SECRET}&page=2",
            f"https://api.example.com/v1/x?api_key={REDACTED}&page={REDACTED}",
        ),
        (f"https://api.example.com/v1/x?{_SECRET}", f"https://api.example.com/v1/x?{REDACTED}"),
        (f"https://user:{_SECRET}@api.example.com/p", "https://api.example.com/p"),
        (f"https://api.example.com/p#{_SECRET}", f"https://api.example.com/p#{REDACTED}"),
        ("https://api.example.com:8443/v1/x", "https://api.example.com:8443/v1/x"),
        ("http://[::1/broken?k=v", REDACTED),
        # Repeated keys, empty values and stray separators: every value masked.
        (
            f"https://h.example/p?k={_SECRET}&k=2&e=&&",
            f"https://h.example/p?k={REDACTED}&k={REDACTED}&e={REDACTED}&&",
        ),
        # ``;`` is not split on, so everything after the first ``=`` is masked.
        (f"https://h.example/p?a=1;key={_SECRET}", f"https://h.example/p?a={REDACTED}"),
        # Percent-encoded names kept verbatim; encoded values masked.
        (f"https://h.example/p?a%3Db={_SECRET}%26x", f"https://h.example/p?a%3Db={REDACTED}"),
        (f"http://[::1]:8080/p?k={_SECRET}", f"http://[::1]:8080/p?k={REDACTED}"),
        (f"https://h.example/p?={_SECRET}", f"https://h.example/p?={REDACTED}"),
    ],
)
def test_redact_url_query_masks_values_keeps_host_and_path(url: str, expected: str) -> None:
    assert redact_url_query(url) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ""),
        (f"code={_SECRET}&state=s", f"code={REDACTED}&state={REDACTED}"),
        (f"{_SECRET}&a=", f"{REDACTED}&a={REDACTED}"),
    ],
)
def test_redact_query_string_masks_values_keeps_names(query: str, expected: str) -> None:
    assert redact_query_string(query) == expected
