"""Unit tests for consent token signing and verification."""

from __future__ import annotations

import time

import pytest

from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.routers.authorize import (
    CONSENT_STATE_MAX_AGE_SECONDS,
    _sign_state,
    _verify_consent_state,
)

SECRET = "test-consent-secret"


def test_valid_consent_token_roundtrip() -> None:
    payload: dict[str, str | None] = {
        "code": "authz-code-123",
        "redirect_uri": "https://app.example.com/cb",
        "client_id": "c1",
        "iat": str(int(time.time())),
    }
    token = _sign_state(payload, SECRET)
    result = _verify_consent_state(token, SECRET)
    assert result["code"] == "authz-code-123"
    assert result["redirect_uri"] == "https://app.example.com/cb"
    assert result["client_id"] == "c1"


def test_expired_consent_token_rejected() -> None:
    expired_iat = str(int(time.time()) - CONSENT_STATE_MAX_AGE_SECONDS - 100)
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": expired_iat,
    }
    token = _sign_state(payload, SECRET)
    with pytest.raises(InvalidGrantError, match="consent expired"):
        _verify_consent_state(token, SECRET)


def test_future_iat_rejected() -> None:
    future_iat = str(int(time.time()) + 100)
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": future_iat,
    }
    token = _sign_state(payload, SECRET)
    with pytest.raises(InvalidGrantError, match="consent expired"):
        _verify_consent_state(token, SECRET)


def test_tampered_signature_rejected() -> None:
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": str(int(time.time())),
    }
    token = _sign_state(payload, SECRET)
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(InvalidGrantError, match="consent signature invalid"):
        _verify_consent_state(tampered, SECRET)


def test_malformed_token_no_dot() -> None:
    with pytest.raises(InvalidGrantError, match="invalid consent token"):
        _verify_consent_state("no-dot-here", SECRET)


def test_wrong_secret_rejected() -> None:
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": str(int(time.time())),
    }
    token = _sign_state(payload, SECRET)
    with pytest.raises(InvalidGrantError, match="consent signature invalid"):
        _verify_consent_state(token, "wrong-secret")
