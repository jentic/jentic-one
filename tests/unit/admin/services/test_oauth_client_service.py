"""Unit tests for OAuth client service — redirect URI validation."""

from __future__ import annotations

import pytest

from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.oauth_client_service import _validate_redirect_uris


def test_accepts_valid_https_uris() -> None:
    _validate_redirect_uris(["https://example.com/callback"])


def test_accepts_multiple_https_uris() -> None:
    _validate_redirect_uris([
        "https://example.com/callback",
        "https://app.example.org/oauth/redirect",
    ])


def test_accepts_http_localhost() -> None:
    _validate_redirect_uris(["http://localhost:3000/callback"])


def test_accepts_http_127_0_0_1() -> None:
    _validate_redirect_uris(["http://127.0.0.1:8080/cb"])


def test_rejects_http_non_localhost() -> None:
    with pytest.raises(InvalidInputError, match="http redirect_uri only allowed for localhost"):
        _validate_redirect_uris(["http://example.com/callback"])


def test_rejects_http_remote_host() -> None:
    with pytest.raises(InvalidInputError, match="http redirect_uri only allowed for localhost"):
        _validate_redirect_uris(["http://192.168.1.1/callback"])


def test_rejects_empty_list() -> None:
    with pytest.raises(InvalidInputError, match="at least one redirect_uri is required"):
        _validate_redirect_uris([])


def test_rejects_uri_without_scheme() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["example.com/callback"])


def test_rejects_uri_without_netloc() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["https://"])


def test_rejects_ftp_scheme() -> None:
    with pytest.raises(InvalidInputError, match="redirect_uri must use https or http"):
        _validate_redirect_uris(["ftp://example.com/callback"])


def test_rejects_javascript_scheme() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["javascript:alert(1)"])
