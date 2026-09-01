"""Unit tests for the authorize router helpers (state signing, redirect validation)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from fastapi import Request

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.routers.authorize import (
    STATE_MAX_AGE_SECONDS,
    _callback_uri,
    _is_allowed_redirect_uri,
    _sign_payload,
    _verify_payload,
)

SECRET = "test-secret-key"


def test_sign_verify_roundtrip() -> None:
    payload: dict[str, str | None] = {
        "client_id": "c1",
        "redirect_uri": "https://app.example.com/cb",
    }
    signed = _sign_payload(payload, SECRET, purpose="state")
    result = _verify_payload(signed, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)
    assert result["client_id"] == "c1"
    assert result["redirect_uri"] == "https://app.example.com/cb"


def test_signature_is_full_sha256() -> None:
    payload: dict[str, str | None] = {"key": "value"}
    signed = _sign_payload(payload, SECRET, purpose="state")
    sig = signed.rsplit(".", 1)[1]
    assert len(sig) == 64


def test_invalid_signature_rejected() -> None:
    payload: dict[str, str | None] = {"key": "value"}
    signed = _sign_payload(payload, SECRET, purpose="state")
    tampered = signed[:-1] + ("a" if signed[-1] != "a" else "b")
    with pytest.raises(InvalidGrantError, match="signature invalid"):
        _verify_payload(tampered, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)


def test_wrong_secret_rejected() -> None:
    payload: dict[str, str | None] = {"key": "value"}
    signed = _sign_payload(payload, SECRET, purpose="state")
    with pytest.raises(InvalidGrantError, match="signature invalid"):
        _verify_payload(signed, "wrong-secret", purpose="state", max_age=STATE_MAX_AGE_SECONDS)


def test_malformed_state_no_dot() -> None:
    with pytest.raises(InvalidGrantError, match="invalid state token"):
        _verify_payload("no-dot-here", SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)


def test_expired_state_rejected() -> None:
    payload: dict[str, str | None] = {
        "key": "value",
        "iat": str(int(time.time()) - STATE_MAX_AGE_SECONDS - 1),
    }
    signed = _sign_payload(payload, SECRET, purpose="state")
    with pytest.raises(InvalidGrantError, match="expired"):
        _verify_payload(signed, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)


def test_future_state_rejected() -> None:
    payload: dict[str, str | None] = {"key": "value", "iat": str(int(time.time()) + 100)}
    signed = _sign_payload(payload, SECRET, purpose="state")
    with pytest.raises(InvalidGrantError, match="expired"):
        _verify_payload(signed, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)


def test_state_within_ttl_accepted() -> None:
    payload: dict[str, str | None] = {"key": "value", "iat": str(int(time.time()) - 60)}
    signed = _sign_payload(payload, SECRET, purpose="state")
    result = _verify_payload(signed, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)
    assert result["key"] == "value"


def test_state_without_iat_accepted() -> None:
    payload: dict[str, str | None] = {"key": "value"}
    signed = _sign_payload(payload, SECRET, purpose="state")
    result = _verify_payload(signed, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)
    assert result["key"] == "value"


def test_purpose_mismatch_rejected() -> None:
    payload: dict[str, str | None] = {"key": "value"}
    signed = _sign_payload(payload, SECRET, purpose="consent")
    with pytest.raises(InvalidGrantError, match="purpose mismatch"):
        _verify_payload(signed, SECRET, purpose="state", max_age=STATE_MAX_AGE_SECONDS)


class _FakeUrl:
    """Minimal stand-in for starlette's URL: str() + .path, like url_for returns."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __str__(self) -> str:
        return self._url

    @property
    def path(self) -> str:
        return urlparse(self._url).path


class _FakeRequest:
    def __init__(self, resolved: str) -> None:
        self._resolved = resolved

    def url_for(self, _name: str) -> _FakeUrl:
        return _FakeUrl(self._resolved)


def test_callback_uri_prefers_canonical_origin_over_request_scheme() -> None:
    request = _FakeRequest("http://internal-host/oauth/callback")
    result = _callback_uri(cast("Request", request), "https://app.example.com")
    assert result == "https://app.example.com/oauth/callback"


def test_callback_uri_keeps_resolved_path() -> None:
    request = _FakeRequest("http://internal-host/oauth/callback")
    result = _callback_uri(cast("Request", request), "https://app.example.com/")
    assert result == "https://app.example.com/oauth/callback"


# ---------- /authorize client validation: the D7 approval gate ----------


def _client_view(*, active: bool = True, approval_status: str = "approved") -> OAuthClientView:
    return OAuthClientView(
        id="oac_1",
        client_id="oc_1",
        name="app",
        description=None,
        redirect_uris=["https://app.example.com/cb"],
        allowed_scopes=None,
        active=active,
        require_consent=True,
        token_endpoint_auth_method="none",
        consent_model="agent",
        registration_source="dcr",
        software_id=None,
        approval_status=approval_status,
        created_at=datetime.now(UTC),
        updated_at=None,
        created_by=None,
    )


class _StateRequest:
    """Request stand-in exposing only ``.state`` (the per-request client cache)."""

    def __init__(self) -> None:
        self.state = SimpleNamespace()


def _ctx_without_platform_clients() -> MagicMock:
    ctx = MagicMock()
    ctx.config.auth.platform_clients = []
    return ctx


@pytest.mark.parametrize(
    ("active", "approval_status", "expected"),
    [
        (True, "approved", True),
        (True, "pending", False),
        (True, "denied", False),
        (False, "approved", False),
    ],
)
async def test_authorize_validation_approval_gate(
    active: bool, approval_status: str, expected: bool
) -> None:
    """Pending/denied clients fail /authorize validation on the existing error
    path even when active and the redirect_uri matches (D7 fails closed)."""
    view = _client_view(active=active, approval_status=approval_status)
    with patch("jentic_one.auth.web.routers.authorize.OAuthClientService") as mock_svc_cls:
        mock_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=view)
        result = await _is_allowed_redirect_uri(
            cast("Request", _StateRequest()),
            "https://app.example.com/cb",
            "oc_1",
            _ctx_without_platform_clients(),
        )
    assert result is expected


async def test_authorize_validation_unknown_client_rejected() -> None:
    with patch("jentic_one.auth.web.routers.authorize.OAuthClientService") as mock_svc_cls:
        mock_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=None)
        result = await _is_allowed_redirect_uri(
            cast("Request", _StateRequest()),
            "https://app.example.com/cb",
            "oc_missing",
            _ctx_without_platform_clients(),
        )
    assert result is False


def test_callback_uri_without_canonical_falls_back_to_request() -> None:
    request = _FakeRequest("http://localhost:8000/oauth/callback")
    assert _callback_uri(cast("Request", request), "") == "http://localhost:8000/oauth/callback"
