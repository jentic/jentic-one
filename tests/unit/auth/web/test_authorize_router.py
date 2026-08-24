"""Unit tests for the authorize router helpers (state signing, redirect validation)."""

from __future__ import annotations

import time
from typing import cast
from urllib.parse import urlparse

import pytest
from fastapi import Request

from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.routers.authorize import (
    STATE_MAX_AGE_SECONDS,
    _callback_uri,
    _matches_canonical_origin,
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


def test_redirect_allowed_path_accepted() -> None:
    assert _matches_canonical_origin(
        "https://app.example.com/oauth/callback", "https://app.example.com"
    )


def test_redirect_allowed_path_with_trailing_slash() -> None:
    assert _matches_canonical_origin(
        "https://app.example.com/auth/callback/", "https://app.example.com/"
    )


def test_redirect_disallowed_path_rejected() -> None:
    assert not _matches_canonical_origin(
        "https://app.example.com/evil/steal-code", "https://app.example.com"
    )


def test_redirect_different_host_rejected() -> None:
    assert not _matches_canonical_origin("https://evil.com/oauth/callback", "https://app.example.com")


def test_redirect_different_scheme_rejected() -> None:
    assert not _matches_canonical_origin(
        "http://app.example.com/oauth/callback", "https://app.example.com"
    )


def test_redirect_no_canonical_url_rejects_all() -> None:
    assert not _matches_canonical_origin("https://app.example.com/oauth/callback", "")


def test_redirect_relative_uri_rejected() -> None:
    assert not _matches_canonical_origin("/oauth/callback", "https://app.example.com")


def test_redirect_different_port_rejected() -> None:
    assert not _matches_canonical_origin(
        "https://app.example.com:9999/oauth/callback", "https://app.example.com"
    )


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


def test_callback_uri_without_canonical_falls_back_to_request() -> None:
    request = _FakeRequest("http://localhost:8000/oauth/callback")
    assert _callback_uri(cast("Request", request), "") == "http://localhost:8000/oauth/callback"
