"""Unit tests for consent token signing and verification."""

from __future__ import annotations

import time

import pytest

from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.flow import (
    CONSENT_STATE_MAX_AGE_SECONDS,
    sign_payload,
    verify_payload,
)

SECRET = "test-consent-secret"


def test_valid_consent_token_roundtrip() -> None:
    payload: dict[str, str | None] = {
        "code": "authz-code-123",
        "redirect_uri": "https://app.example.com/cb",
        "client_id": "c1",
        "iat": str(int(time.time())),
    }
    token = sign_payload(payload, SECRET, purpose="consent")
    result = verify_payload(token, SECRET, purpose="consent", max_age=CONSENT_STATE_MAX_AGE_SECONDS)
    assert result["code"] == "authz-code-123"
    assert result["redirect_uri"] == "https://app.example.com/cb"
    assert result["client_id"] == "c1"


def test_expired_consent_token_rejected() -> None:
    expired_iat = str(int(time.time()) - CONSENT_STATE_MAX_AGE_SECONDS - 100)
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": expired_iat,
    }
    token = sign_payload(payload, SECRET, purpose="consent")
    with pytest.raises(InvalidGrantError, match="expired"):
        verify_payload(token, SECRET, purpose="consent", max_age=CONSENT_STATE_MAX_AGE_SECONDS)


def test_future_iat_rejected() -> None:
    future_iat = str(int(time.time()) + 100)
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": future_iat,
    }
    token = sign_payload(payload, SECRET, purpose="consent")
    with pytest.raises(InvalidGrantError, match="expired"):
        verify_payload(token, SECRET, purpose="consent", max_age=CONSENT_STATE_MAX_AGE_SECONDS)


def test_tampered_signature_rejected() -> None:
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": str(int(time.time())),
    }
    token = sign_payload(payload, SECRET, purpose="consent")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(InvalidGrantError, match="signature invalid"):
        verify_payload(tampered, SECRET, purpose="consent", max_age=CONSENT_STATE_MAX_AGE_SECONDS)


def test_malformed_token_no_dot() -> None:
    with pytest.raises(InvalidGrantError, match="invalid consent token"):
        verify_payload(
            "no-dot-here", SECRET, purpose="consent", max_age=CONSENT_STATE_MAX_AGE_SECONDS
        )


def test_wrong_secret_rejected() -> None:
    payload: dict[str, str | None] = {
        "code": "authz-code",
        "iat": str(int(time.time())),
    }
    token = sign_payload(payload, SECRET, purpose="consent")
    with pytest.raises(InvalidGrantError, match="signature invalid"):
        verify_payload(
            token, "wrong-secret", purpose="consent", max_age=CONSENT_STATE_MAX_AGE_SECONDS
        )
