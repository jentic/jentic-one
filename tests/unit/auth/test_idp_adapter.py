"""Unit tests for the IdP adapter protocol and OIDC adapter."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from jentic_one.auth.core.idp import OidcAdapter
from jentic_one.auth.core.idp.adapter import IdpClaims
from jentic_one.shared.config import IdpConfig


@pytest.fixture()
def idp_config() -> IdpConfig:
    return IdpConfig(
        enabled=True,
        provider="oidc",
        issuer="https://idp.example.com",
        client_id="test-client",
        client_secret=SecretStr("test-secret"),
        scopes=["openid", "email", "profile"],
    )


@pytest.fixture()
def adapter(idp_config: IdpConfig) -> OidcAdapter:
    return OidcAdapter(idp_config)


def test_authorize_url_valid(adapter: OidcAdapter) -> None:
    url = adapter.authorize_url(
        state="test-state", nonce="test-nonce", redirect_uri="https://app.example.com/callback"
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "idp.example.com"
    assert parsed.path == "/authorize"


def test_authorize_url_includes_required_params(adapter: OidcAdapter) -> None:
    url = adapter.authorize_url(
        state="test-state", nonce="test-nonce", redirect_uri="https://app.example.com/callback"
    )
    params = parse_qs(urlparse(url).query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["test-client"]
    assert params["redirect_uri"] == ["https://app.example.com/callback"]
    assert params["state"] == ["test-state"]
    assert params["nonce"] == ["test-nonce"]
    assert params["scope"] == ["openid email profile"]


def test_authorize_url_uses_custom_endpoint() -> None:
    config = IdpConfig(
        enabled=True,
        issuer="https://idp.example.com",
        client_id="test-client",
        client_secret=SecretStr("secret"),
        authorization_endpoint="https://idp.example.com/custom/auth",
    )
    adapter = OidcAdapter(config)
    url = adapter.authorize_url(state="s", nonce="n", redirect_uri="https://app.example.com/cb")
    assert url.startswith("https://idp.example.com/custom/auth?")


def test_map_claims_standard(adapter: OidcAdapter) -> None:
    userinfo: dict[str, object] = {
        "sub": "ext-user-123",
        "email": "user@example.com",
        "given_name": "Jane",
        "family_name": "Doe",
    }
    claims = adapter.map_claims(userinfo)
    assert claims == IdpClaims(
        external_subject="ext-user-123",
        email="user@example.com",
        first_name="Jane",
        last_name="Doe",
    )


def test_map_claims_handles_missing_optional(adapter: OidcAdapter) -> None:
    userinfo: dict[str, object] = {"sub": "ext-user-123", "email": "user@example.com"}
    claims = adapter.map_claims(userinfo)
    assert claims.first_name == ""
    assert claims.last_name == ""
    assert claims.email_verified is False


def test_map_claims_email_verified_true(adapter: OidcAdapter) -> None:
    userinfo: dict[str, object] = {
        "sub": "ext-user-123",
        "email": "user@example.com",
        "email_verified": True,
    }
    claims = adapter.map_claims(userinfo)
    assert claims.email_verified is True


def test_map_claims_email_verified_false(adapter: OidcAdapter) -> None:
    userinfo: dict[str, object] = {
        "sub": "ext-user-123",
        "email": "user@example.com",
        "email_verified": False,
    }
    claims = adapter.map_claims(userinfo)
    assert claims.email_verified is False


# ── Google provider profile (WI-2) ───────────────────────────────────────────


@pytest.fixture()
def google_config() -> IdpConfig:
    return IdpConfig(
        enabled=True,
        provider="google",
        client_id="google-client",
        client_secret=SecretStr("google-secret"),
    )


def test_google_defaults_authorize_endpoint(google_config: IdpConfig) -> None:
    adapter = OidcAdapter(google_config)
    url = adapter.authorize_url(state="s", nonce="n", redirect_uri="https://app.example.com/cb")
    parsed = urlparse(url)
    assert parsed.hostname == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"


def test_google_explicit_endpoint_overrides_default() -> None:
    config = IdpConfig(
        enabled=True,
        provider="google",
        client_id="google-client",
        client_secret=SecretStr("secret"),
        authorization_endpoint="https://custom.example.com/auth",
    )
    adapter = OidcAdapter(config)
    url = adapter.authorize_url(state="s", nonce="n", redirect_uri="https://app.example.com/cb")
    assert url.startswith("https://custom.example.com/auth?")


def test_google_map_claims_surfaces_hd(google_config: IdpConfig) -> None:
    adapter = OidcAdapter(google_config)
    claims = adapter.map_claims(
        {
            "sub": "g-1",
            "email": "user@jentic.com",
            "email_verified": True,
            "hd": "jentic.com",
        }
    )
    assert claims.hosted_domain == "jentic.com"
    assert claims.email_verified is True


def test_map_claims_hosted_domain_none_without_hd(adapter: OidcAdapter) -> None:
    # Generic OIDC (and consumer Google) accounts carry no `hd` claim.
    claims = adapter.map_claims({"sub": "x", "email": "user@example.com"})
    assert claims.hosted_domain is None
