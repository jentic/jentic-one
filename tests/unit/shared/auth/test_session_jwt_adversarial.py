"""Tests for session-JWT claim validation and algorithm enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.shared.auth.tokens import InvalidTokenError, decode_jwt, issue_jwt

_SECRET = "a-valid-32-byte-signing-secret!!"  # pragma: allowlist secret


def test_decode_jwt_accepts_valid_token_with_required_claims() -> None:
    token = issue_jwt({"sub": "usr_123", "actor_type": "user"}, _SECRET, ttl_seconds=3600)
    claims = decode_jwt(token, _SECRET)
    assert claims["sub"] == "usr_123"
    assert "exp" in claims
    assert "iat" in claims


@pytest.mark.parametrize("missing_claim", ["exp", "iat", "sub"])
def test_decode_jwt_rejects_token_missing_required_claim(missing_claim: str) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": "usr_123",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
    }
    del payload[missing_claim]
    token = jwt.encode(payload, _SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_jwt(token, _SECRET)


def test_decode_jwt_rejects_alg_none() -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": "usr_123",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
    }
    token = jwt.encode(payload, "", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_jwt(token, _SECRET)


def test_decode_jwt_rejects_hs512_with_same_secret() -> None:
    now = datetime.now(UTC)
    secret_64 = "b" * 64
    payload = {
        "sub": "usr_123",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
    }
    token = jwt.encode(payload, secret_64, algorithm="HS512")
    with pytest.raises(InvalidTokenError):
        decode_jwt(token, secret_64)


def test_decode_jwt_rejects_connect_state_token_signed_with_state_secret() -> None:
    state_secret = "separate-32-byte-connect-secret!"  # pragma: allowlist secret
    state = ConnectState(
        credential_id="cred_1",
        provider="direct_oauth2",
        actor_id="usr_123",
        issued_at=datetime.now(UTC),
        nonce=generate_nonce(),
    )
    state_token = encode_state(state_secret, state, ttl_seconds=600)
    with pytest.raises(InvalidTokenError):
        decode_jwt(state_token, _SECRET)
