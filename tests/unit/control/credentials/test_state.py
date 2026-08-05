"""Tests for the connect-state codec."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import (
    StateExpiredError,
    StateInvalidError,
    decode_state,
    encode_state,
    generate_nonce,
)

SECRET = "test-secret-for-state-signing"


def _make_state(
    credential_id: str = "cred_abc123",
    provider: str = "direct_oauth2",
    nonce: str | None = None,
) -> ConnectState:
    return ConnectState(
        credential_id=credential_id,
        provider=provider,
        actor_id="user_xyz",
        issued_at=datetime.now(UTC),
        nonce=nonce or generate_nonce(),
    )


def test_generate_nonce_uniqueness() -> None:
    nonces = {generate_nonce() for _ in range(100)}
    assert len(nonces) == 100


def test_encode_decode_roundtrip() -> None:
    state = _make_state()
    token = encode_state(SECRET, state, ttl_seconds=600)
    decoded = decode_state(SECRET, token)

    assert decoded.credential_id == state.credential_id
    assert decoded.provider == state.provider
    assert decoded.actor_id == state.actor_id
    assert decoded.nonce == state.nonce


def test_decode_with_wrong_secret_raises() -> None:
    state = _make_state()
    token = encode_state(SECRET, state, ttl_seconds=600)

    with pytest.raises(StateInvalidError):
        decode_state("wrong-secret", token)


def test_decode_tampered_token_raises() -> None:
    state = _make_state()
    token = encode_state(SECRET, state, ttl_seconds=600)
    tampered = token[:-5] + "XXXXX"

    with pytest.raises(StateInvalidError):
        decode_state(SECRET, tampered)


def test_decode_expired_token_raises() -> None:
    state = _make_state()
    token = encode_state(SECRET, state, ttl_seconds=1)
    time.sleep(2)

    with pytest.raises(StateExpiredError):
        decode_state(SECRET, token)


def test_decode_garbage_raises() -> None:
    with pytest.raises(StateInvalidError):
        decode_state(SECRET, "not-a-valid-jwt-at-all")


def test_state_preserves_none_actor_id() -> None:
    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id=None,
        issued_at=datetime.now(UTC),
        nonce=generate_nonce(),
    )
    token = encode_state(SECRET, state, ttl_seconds=600)
    decoded = decode_state(SECRET, token)
    assert decoded.actor_id is None


def test_state_roundtrips_redirect_uri() -> None:
    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id="user_1",
        issued_at=datetime.now(UTC),
        nonce=generate_nonce(),
        redirect_uri="http://127.0.0.1:8020/credentials/oauth/callback",
    )
    token = encode_state(SECRET, state, ttl_seconds=600)
    decoded = decode_state(SECRET, token)
    assert decoded.redirect_uri == "http://127.0.0.1:8020/credentials/oauth/callback"


def test_state_without_redirect_uri_decodes_to_none() -> None:
    # A state minted before the ``ruri`` claim existed (rolling upgrade) still
    # decodes; the missing claim maps to None so complete_connect can fall back
    # to the configured redirect_uri.
    state = _make_state()
    token = encode_state(SECRET, state, ttl_seconds=600)
    decoded = decode_state(SECRET, token)
    assert decoded.redirect_uri is None
