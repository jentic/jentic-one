"""Unit tests for central secret redaction (§08 E3.2)."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from typing import Any

import pytest
import structlog

from jentic_one.auth.services.crypto import generate_agent_api_key, generate_client_secret
from jentic_one.auth.services.registration_service import RAT_PREFIX
from jentic_one.auth.services.token_service import ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX
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
        "code_verifier",
        "pkce_code_verifier",
        "device_code",
        "client_assertion",
        "assertion",
        "passphrase",
        "private-key",
        "privatekey",
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


@pytest.mark.parametrize(
    ("raw", "secret", "expected"),
    [
        (
            '{"access_token":"abc\'def","safe":"ok"}',
            "abc'def",
            '{"access_token":"***REDACTED***","safe":"ok"}',
        ),
        (
            "{'access_token':'abc\"def','safe':'ok'}",
            'abc"def',
            "{'access_token':'***REDACTED***','safe':'ok'}",
        ),
        (
            r'{"access_token":"abc\"def","safe":"ok"}',
            r"abc\"def",
            r'{"access_token":"***REDACTED***","safe":"ok"}',
        ),
        (
            r"{'access_token':'abc\'def','safe':'ok'}",
            r"abc\'def",
            r"{'access_token':'***REDACTED***','safe':'ok'}",
        ),
        (
            '{"access_token":"unterminated\'value',
            "unterminated'value",
            '{"access_token":"***REDACTED***',
        ),
    ],
)
def test_embedded_json_secret_redaction_respects_value_delimiter(
    raw: str, secret: str, expected: str
) -> None:
    out = redact_value({"detail": raw})["detail"]

    assert out == expected
    assert secret not in out


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


def test_free_text_header_and_bare_jwt_are_masked() -> None:
    jwt_token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."  # pragma: allowlist secret
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."  # pragma: allowlist secret
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"  # pragma: allowlist secret
    )
    samples = [
        (
            "Authorization: Token abcdef123456",
            "abcdef123456",  # pragma: allowlist secret
            "Authorization",
        ),
        ("Proxy-Authorization: Token proxysecret99", "proxysecret99", "Proxy-Authorization"),
        ("X-Api-Key: keysecret123", "keysecret123", "X-Api-Key"),
        ("Api-Key: barekeysecret456", "barekeysecret456", "Api-Key"),
        ("X-Auth-Token: authtokensecret789", "authtokensecret789", "X-Auth-Token"),
        (f"upstream returned {jwt_token} in error", jwt_token, "upstream returned"),
    ]
    for raw, secret, kept in samples:
        out = redact_value(raw)
        assert secret not in out, (raw, out)
        assert kept in out
        assert REDACTED in out
        assert redact_value(out) == out


def test_code_verifier_in_query_is_masked() -> None:
    raw = (
        "?code=AUTHCODE&code_verifier=VERIFIER_SECRET"
        "&client_assertion=CLIENT_ASSERTION_SECRET&assertion=ASSERTION_SECRET"
    )
    out = redact_value(raw)
    assert "AUTHCODE" not in out
    assert "VERIFIER_SECRET" not in out
    assert "CLIENT_ASSERTION_SECRET" not in out
    assert "ASSERTION_SECRET" not in out
    assert f"code_verifier={REDACTED}" in out
    assert f"client_assertion={REDACTED}" in out
    assert f"assertion={REDACTED}" in out


def test_error_code_and_state_and_user_code_not_redacted() -> None:
    payload = {
        "error_code": "invalid_request",
        "state": "polling",
        "user_code": "WDJB-MJHT",
    }
    assert redact_value(payload) == payload


def test_status_code_key_not_redacted() -> None:
    assert redact_value({"code": 404}) == {"code": 404}


def _prefixed_token(prefix: str) -> Callable[[], str]:
    """The token shape the auth services issue: prefix + ``token_urlsafe(32)``."""
    return lambda: f"{prefix}{secrets.token_urlsafe(32)}"


@pytest.mark.parametrize(
    ("make_token", "prefix"),
    [
        (generate_agent_api_key, "jak_"),
        (generate_client_secret, "jcs_"),
        (_prefixed_token(ACCESS_TOKEN_PREFIX), "at_"),
        (_prefixed_token(REFRESH_TOKEN_PREFIX), "rt_"),
        (_prefixed_token(RAT_PREFIX), "rat_"),
    ],
)
@pytest.mark.parametrize(
    "template",
    [
        "{token}",
        "upstream rejected key {token} for agent agnt_1",
        "config: api_key_value={token}, retrying",
        "('{token}',)",
        "line one\n{token}\nline two",
    ],
)
def test_bare_platform_token_in_free_text_is_masked(
    make_token: Callable[[], str], prefix: str, template: str
) -> None:
    token = make_token()
    assert token.startswith(prefix)
    out = redact_value(template.format(token=token))
    assert token not in out
    assert token[len(prefix) :] not in out
    assert f"{prefix}{REDACTED}" in out
    assert redact_value(out) == out


def test_bare_platform_token_with_leading_dash_or_underscore_body_is_masked() -> None:
    # token_urlsafe output can start with "-" or "_"; the prefix still anchors it.
    for body in ("-" + "a" * 42, "_" + "B" * 42):
        assert redact_value(f"key jak_{body} denied") == f"key jak_{REDACTED} denied"


@pytest.mark.parametrize(
    "text",
    [
        # Ordinary words and identifiers containing a token prefix.
        "the request was retried at_least once",
        "created_at_utc=2026-01-01T00:00:00Z",
        "sort by updated_at_desc then created_at_asc",
        "format_v2 and that_one and rt_flag and rat_race",
        "jak_ prefix is documented, jak_example-secret is the docs example",
        # A token-shaped body glued to a longer identifier is not a token.
        "created_at_" + "a" * 43,
        "x" + "jak_" + "A" * 43,
        "jak_" + "A" * 44,
        # Ids: a KSUID (27 characters) behind a short prefix, and a 43-character
        # body that is too short for its prefix's issuance shape.
        "agnt_2fXyZ8b3QkLmNoPqRsTuVwXyZaB and at_2fXyZ8b3QkLmNoPqRsTuVwXyZaB",
        "at_" + "a" * 42,
        # Provider-key look-alikes.
        "sk-learn-is-a-library-name-not-a-key",
        "risk-0123456789abcdefghijklmnop",  # pragma: allowlist secret
        "sk-1234",
        "AKIA is the access-key id prefix",
        "XAKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
        "AKIAIOSFODNN7EXAMPLE1",  # pragma: allowlist secret
    ],
)
def test_token_lookalikes_in_free_text_untouched(text: str) -> None:
    assert redact_value(text) == text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "openai said invalid key sk-proj-AbC123dEf456GhI789jKl0mNoPq",
            f"openai said invalid key sk-{REDACTED}",
        ),
        (
            "key sk-ant-api03-AbC123dEf456GhI789jKl0mNoPq-xyz rejected",  # pragma: allowlist secret
            f"key sk-{REDACTED} rejected",
        ),
        (
            "legacy sk-AbCdEfGhIjKlMnOpQrStUvWx1234 expired",
            f"legacy sk-{REDACTED} expired",
        ),
        (
            "signing with AKIAIOSFODNN7EXAMPLE failed",  # pragma: allowlist secret
            f"signing with AKIA{REDACTED} failed",
        ),
        (
            "session ASIAY34FZKBOKMUTVV7A expired",  # pragma: allowlist secret
            f"session ASIA{REDACTED} expired",
        ),
    ],
)
def test_provider_key_shapes_in_free_text_are_masked(raw: str, expected: str) -> None:
    assert redact_value(raw) == expected


def test_bearer_platform_token_keeps_single_mask() -> None:
    token = generate_agent_api_key()
    assert redact_value(f"Authorization: Bearer {token}") == f"Authorization: Bearer {REDACTED}"
    assert redact_value(f"sent Bearer {token}") == f"sent Bearer {REDACTED}"
