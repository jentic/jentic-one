"""Tests for credential service schemas validation."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from jentic_one.control.services.credentials.schemas import (
    ConnectCallback,
    ConnectRequest,
    ConnectState,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.control.services.credentials.schemas.connect import AuthCodeChallenge


def test_connect_request_defaults() -> None:
    req = ConnectRequest()
    assert req.scopes == []
    assert req.extra == {}


def test_connect_challenge_validates() -> None:
    c = AuthCodeChallenge(authorize_url="https://example.com/auth", state="abc123")
    assert c.authorize_url == "https://example.com/auth"
    assert c.state == "abc123"
    assert c.kind == "authorization_code"


def test_connect_state_validates() -> None:
    now = datetime.now(UTC)
    s = ConnectState(
        credential_id="cred_123",
        provider="static",
        actor_id="user_456",
        issued_at=now,
        nonce="xyz",
    )
    assert s.credential_id == "cred_123"
    assert s.provider == "static"
    assert s.actor_id == "user_456"
    assert s.issued_at == now
    assert s.nonce == "xyz"


def test_connect_callback_all_none() -> None:
    cb = ConnectCallback()
    assert cb.code is None
    assert cb.error is None
    assert cb.raw == {}


def test_provision_result_defaults() -> None:
    r = ProvisionResult()
    assert r.access_token is None
    assert r.refresh_token is None
    assert r.expires_at is None
    assert r.scope is None
    assert r.provider_account_ref is None


def test_provision_result_full() -> None:
    now = datetime.now(UTC)
    r = ProvisionResult(
        access_token="tok",
        refresh_token="ref",
        expires_at=now,
        scope="read write",
        provider_account_ref="acct_1",
    )
    assert r.access_token == "tok"
    assert r.expires_at == now


def test_refresh_result_requires_access_token() -> None:
    try:
        RefreshResult()  # type: ignore[call-arg]
    except ValidationError:
        pass
    else:
        raise AssertionError("Expected ValidationError for missing access_token")


def test_refresh_result_validates() -> None:
    r = RefreshResult(access_token="new_tok")
    assert r.access_token == "new_tok"
    assert r.expires_at is None
