"""Unit tests for the IdP adapter protocol and OIDC adapter."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from jentic_one.auth.core.idp import (
    GoogleOidcAdapter,
    OidcAdapter,
    build_idp_adapter,
)
from jentic_one.auth.core.idp.adapter import IdpClaims
from jentic_one.auth.services.errors import InvalidGrantError
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


def test_authorize_url_includes_code_challenge_when_supplied(adapter: OidcAdapter) -> None:
    url = adapter.authorize_url(
        state="test-state",
        nonce="test-nonce",
        redirect_uri="https://app.example.com/callback",
        code_challenge="challenge-123",
    )
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge"] == ["challenge-123"]
    assert params["code_challenge_method"] == ["S256"]


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
        issuer="https://idp.example.com",
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        ("true", True),
        ("TRUE", True),
        (" True ", True),
        (False, False),
        ("false", False),
        ("False", False),
        ("", False),
        ("yes", False),
        ("1", False),
        (1, False),
        (None, False),
    ],
)
def test_map_claims_email_verified_is_strict(
    adapter: OidcAdapter, raw: object, expected: bool
) -> None:
    """Only boolean true or the string "true" count as verified; "false" does not."""
    userinfo: dict[str, object] = {
        "sub": "ext-user-123",
        "email": "user@example.com",
        "email_verified": raw,
    }
    assert adapter.map_claims(userinfo).email_verified is expected


def test_google_map_claims_string_false_is_unverified(google_config: IdpConfig) -> None:
    adapter = GoogleOidcAdapter(google_config)
    claims = adapter.map_claims(
        {"sub": "g-1", "email": "user@example.com", "email_verified": "false"}
    )
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
    adapter = GoogleOidcAdapter(google_config)
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
    adapter = GoogleOidcAdapter(config)
    url = adapter.authorize_url(state="s", nonce="n", redirect_uri="https://app.example.com/cb")
    assert url.startswith("https://custom.example.com/auth?")


def test_google_map_claims_surfaces_hd(google_config: IdpConfig) -> None:
    adapter = GoogleOidcAdapter(google_config)
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


def test_google_map_claims_hosted_domain_none_without_hd(google_config: IdpConfig) -> None:
    # Consumer Google accounts carry no `hd` claim.
    adapter = GoogleOidcAdapter(google_config)
    claims = adapter.map_claims({"sub": "x", "email": "user@gmail.com"})
    assert claims.hosted_domain is None


def test_generic_adapter_never_sets_hosted_domain(adapter: OidcAdapter) -> None:
    # The generic OIDC adapter leaves hosted_domain None even if `hd` is present
    # — `hd` handling is a Google-specific concern.
    claims = adapter.map_claims({"sub": "x", "email": "user@example.com", "hd": "example.com"})
    assert claims.hosted_domain is None


# ── Adapter factory ───────────────────────────────────────────────────────────


def test_factory_returns_none_when_disabled() -> None:
    config = IdpConfig(enabled=False, provider="google")
    assert build_idp_adapter(config) is None


def test_factory_selects_google_adapter(google_config: IdpConfig) -> None:
    assert isinstance(build_idp_adapter(google_config), GoogleOidcAdapter)


def test_factory_falls_back_to_generic_for_standard_oidc(idp_config: IdpConfig) -> None:
    adapter = build_idp_adapter(idp_config)
    assert type(adapter) is OidcAdapter


# ── Upstream exchange_code: PKCE, id_token verification, userinfo sub ────────


_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_JWK: dict[str, Any] = {
    **jwt.api_jwk.PyJWK.from_json(RSAAlgorithm.to_jwk(_RSA_KEY.public_key()))._jwk_data,
    "kid": "idp-kid-1",
    "use": "sig",
    "alg": "RS256",
}


def _sign_upstream_id_token(
    *,
    iss: str = "https://idp.example.com",
    sub: str = "ext-user-123",
    aud: str = "test-client",
    nonce: str | None = "flow-nonce-1",
    exp_offset: int = 300,
    kid: str | None = "idp-kid-1",
    key: rsa.RSAPrivateKey = _RSA_KEY,
) -> str:
    payload: dict[str, Any] = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    if nonce is not None:
        payload["nonce"] = nonce
    headers: dict[str, Any] = {}
    if kid is not None:
        headers["kid"] = kid
    return jwt.encode(payload, key, algorithm="RS256", headers=headers)


@respx.mock
async def test_exchange_code_verifies_id_token_and_sends_pkce(adapter: OidcAdapter) -> None:
    id_token = _sign_upstream_id_token()
    token_route = respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "up-tok", "id_token": id_token},
        )
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(
            200,
            json={"jwks_uri": "https://idp.example.com/keys"},
        )
    )
    respx.get("https://idp.example.com/keys").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )
    respx.get("https://idp.example.com/userinfo").mock(
        return_value=httpx.Response(
            200,
            json={"sub": "ext-user-123", "email": "user@example.com", "email_verified": True},
        )
    )

    userinfo = await adapter.exchange_code(
        "auth-code-1",
        redirect_uri="https://app.example.com/oauth/callback",
        code_verifier="verifier-xyz",
        nonce="flow-nonce-1",
    )

    assert userinfo["sub"] == "ext-user-123"
    assert userinfo["iss"] == "https://idp.example.com"
    sent_form = parse_qs(token_route.calls[0].request.content.decode())
    assert sent_form["code_verifier"] == ["verifier-xyz"]


@respx.mock
async def test_exchange_code_rejects_missing_id_token(adapter: OidcAdapter) -> None:
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok"})
    )
    with pytest.raises(InvalidGrantError, match="missing id_token"):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@respx.mock
async def test_exchange_code_rejects_invalid_id_token_signature(adapter: OidcAdapter) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    id_token = _sign_upstream_id_token(key=other_key)
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )

    with pytest.raises(InvalidGrantError, match="verification failed"):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@pytest.mark.parametrize(
    ("token_kwargs", "match"),
    [
        ({"iss": "https://other-idp.example.com"}, "issuer mismatch"),
        ({"aud": "wrong-client"}, "verification failed"),
        ({"exp_offset": -60}, "verification failed"),
        ({"nonce": "wrong-nonce"}, "nonce mismatch"),
        ({"nonce": None}, "verification failed"),
    ],
)
@respx.mock
async def test_exchange_code_rejects_bad_id_token_claims(
    adapter: OidcAdapter,
    token_kwargs: dict[str, Any],
    match: str,
) -> None:
    id_token = _sign_upstream_id_token(**token_kwargs)
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )

    with pytest.raises(InvalidGrantError, match=match):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@respx.mock
async def test_exchange_code_rejects_userinfo_sub_mismatch(adapter: OidcAdapter) -> None:
    id_token = _sign_upstream_id_token(sub="sub-from-id-token")
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )
    respx.get("https://idp.example.com/userinfo").mock(
        return_value=httpx.Response(
            200,
            json={"sub": "different-sub", "email": "user@example.com", "email_verified": True},
        )
    )

    with pytest.raises(InvalidGrantError, match="sub does not match"):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@pytest.mark.parametrize("alg", ["HS256", "none"])
@respx.mock
async def test_exchange_code_rejects_symmetric_or_none_id_token_algorithm(
    adapter: OidcAdapter, alg: str
) -> None:
    payload: dict[str, Any] = {
        "iss": "https://idp.example.com",
        "sub": "ext-user-123",
        "aud": "test-client",
        "nonce": "flow-nonce-1",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    key = "shared-secret" if alg == "HS256" else None
    id_token = jwt.encode(payload, key, algorithm=alg, headers={"kid": "idp-kid-1"})  # type: ignore[arg-type]
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )

    with pytest.raises(InvalidGrantError, match="Unsupported upstream id_token algorithm"):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@respx.mock
async def test_exchange_code_rejects_empty_nonce(adapter: OidcAdapter) -> None:
    id_token = _sign_upstream_id_token(nonce="")
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )

    with pytest.raises(InvalidGrantError, match="nonce mismatch"):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@respx.mock
async def test_exchange_code_rejects_userinfo_iss_mismatch(adapter: OidcAdapter) -> None:
    id_token = _sign_upstream_id_token()
    respx.post("https://idp.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )
    respx.get("https://idp.example.com/userinfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "sub": "ext-user-123",
                "iss": "https://other-idp.example.com",
                "email": "user@example.com",
                "email_verified": True,
            },
        )
    )

    with pytest.raises(InvalidGrantError, match="iss does not match"):
        await adapter.exchange_code(
            "auth-code-1",
            redirect_uri="https://app.example.com/oauth/callback",
            nonce="flow-nonce-1",
        )


@respx.mock
async def test_exchange_code_caches_jwks_and_refreshes_on_key_rotation(
    adapter: OidcAdapter,
) -> None:
    rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rotated_jwk: dict[str, Any] = {
        **jwt.api_jwk.PyJWK.from_json(RSAAlgorithm.to_jwk(rotated_key.public_key()))._jwk_data,
        "kid": "idp-kid-2",
        "use": "sig",
        "alg": "RS256",
    }
    first_token = _sign_upstream_id_token(kid="idp-kid-1", key=_RSA_KEY)
    second_token = _sign_upstream_id_token(kid="idp-kid-2", key=rotated_key)

    respx.post("https://idp.example.com/oauth/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "up-tok-1", "id_token": first_token}),
            httpx.Response(200, json={"access_token": "up-tok-1", "id_token": first_token}),
            httpx.Response(200, json={"access_token": "up-tok-2", "id_token": second_token}),
        ]
    )
    respx.get("https://idp.example.com/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    jwks_route = respx.get("https://idp.example.com/.well-known/jwks.json").mock(
        side_effect=[
            httpx.Response(200, json={"keys": [_RSA_JWK]}),
            httpx.Response(200, json={"keys": [rotated_jwk]}),
        ]
    )
    respx.get("https://idp.example.com/userinfo").mock(
        return_value=httpx.Response(
            200,
            json={"sub": "ext-user-123", "email": "user@example.com", "email_verified": True},
        )
    )

    # First call fetches JWKS once.
    await adapter.exchange_code(
        "auth-code-1",
        redirect_uri="https://app.example.com/oauth/callback",
        nonce="flow-nonce-1",
    )
    assert jwks_route.call_count == 1

    # Second call with same kid uses cached JWKS without another network fetch.
    await adapter.exchange_code(
        "auth-code-2",
        redirect_uri="https://app.example.com/oauth/callback",
        nonce="flow-nonce-1",
    )
    assert jwks_route.call_count == 1

    # Third call with rotated kid triggers a single forced JWKS refresh and succeeds.
    userinfo = await adapter.exchange_code(
        "auth-code-3",
        redirect_uri="https://app.example.com/oauth/callback",
        nonce="flow-nonce-1",
    )
    assert jwks_route.call_count == 2
    assert userinfo["sub"] == "ext-user-123"


@pytest.mark.parametrize(
    ("configured_issuer", "token_iss", "accepted"),
    [
        (None, "https://accounts.google.com", True),
        (None, "accounts.google.com", True),
        (None, "https://accounts.google.com/", True),
        (None, "https://other-idp.example.com", False),
        # The bare spelling is Google's own: a custom issuer never accepts it.
        ("https://idp.example.com", "accounts.google.com", False),
    ],
)
@respx.mock
async def test_google_exchange_code_accepts_both_documented_issuer_spellings(
    configured_issuer: str | None, token_iss: str, accepted: bool
) -> None:
    """Google ID tokens carry either issuer spelling; both key the link under one issuer."""
    config = IdpConfig(
        enabled=True,
        provider="google",
        client_id="google-client",
        client_secret=SecretStr("google-secret"),
        issuer=configured_issuer or "",
    )
    adapter = GoogleOidcAdapter(config)
    id_token = _sign_upstream_id_token(iss=token_iss, aud="google-client")
    respx.post(adapter._token_endpoint).mock(
        return_value=httpx.Response(200, json={"access_token": "up-tok", "id_token": id_token})
    )
    respx.get(f"{adapter.issuer}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(404)
    )
    respx.get(adapter._default_jwks_uri()).mock(
        return_value=httpx.Response(200, json={"keys": [_RSA_JWK]})
    )
    respx.get(adapter._userinfo_endpoint).mock(
        return_value=httpx.Response(
            200, json={"sub": "ext-user-123", "email": "u@example.com", "email_verified": True}
        )
    )

    call = adapter.exchange_code(
        "auth-code-1",
        redirect_uri="https://app.example.com/oauth/callback",
        nonce="flow-nonce-1",
    )
    if not accepted:
        with pytest.raises(InvalidGrantError, match="issuer mismatch"):
            await call
        return
    userinfo = await call
    assert userinfo["iss"] == "https://accounts.google.com"
    assert adapter.map_claims(userinfo).issuer == "https://accounts.google.com"
