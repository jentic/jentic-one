"""Tests for the connect-state codec."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime

import pytest

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import (
    BROWSER_BINDING_COOKIE_PREFIX,
    BrowserBindingClaim,
    StateExpiredError,
    StateInvalidError,
    browser_binding_matches,
    decode_state,
    encode_state,
    generate_nonce,
    new_browser_binding,
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


# --- browser binding ---


def test_new_browser_binding_hashes_its_value() -> None:
    binding = new_browser_binding(host="localhost")
    assert binding.digest == hashlib.sha256(binding.value.encode()).hexdigest()
    assert binding.claim == BrowserBindingClaim(digest=binding.digest, host="localhost")
    assert binding.cookie_name == f"{BROWSER_BINDING_COOKIE_PREFIX}{binding.digest[:16]}"
    # The raw value is the secret half: never in a repr (and so never in a log line).
    assert binding.value not in repr(binding)


def test_new_browser_binding_is_random_per_flow() -> None:
    assert len({new_browser_binding().value for _ in range(50)}) == 50


def test_state_roundtrips_browser_binding_claims() -> None:
    binding = new_browser_binding(host="127.0.0.1")
    state = _make_state().model_copy(
        update={"browser_binding_hash": binding.digest, "browser_host": binding.host}
    )
    decoded = decode_state(SECRET, encode_state(SECRET, state, ttl_seconds=600))
    assert decoded.browser_binding_hash == binding.digest
    assert decoded.browser_host == "127.0.0.1"


def test_state_without_browser_binding_decodes_to_none() -> None:
    decoded = decode_state(SECRET, encode_state(SECRET, _make_state(), ttl_seconds=600))
    assert decoded.browser_binding_hash is None
    assert decoded.browser_host is None


def test_browser_binding_matches_only_the_minted_value() -> None:
    binding = new_browser_binding()
    state = _make_state().model_copy(update={"browser_binding_hash": binding.digest})
    assert browser_binding_matches(state, binding.value)
    assert not browser_binding_matches(state, new_browser_binding().value)
    assert not browser_binding_matches(state, None)
    assert not browser_binding_matches(state, "")
    # The hash itself is not the cookie value.
    assert not browser_binding_matches(state, binding.digest)


def test_unbound_state_never_matches() -> None:
    """A state signed before binding existed is refused, never accepted unbound."""
    assert not browser_binding_matches(_make_state(), new_browser_binding().value)
