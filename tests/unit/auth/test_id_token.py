"""Unit tests for ID token issuance and JWKS construction."""

from __future__ import annotations

import jwt
import pytest
from pydantic import SecretStr

from jentic_one.auth.core.id_token import get_active_kid, issue_id_token
from jentic_one.shared.auth import CachedJWKSPublisher
from jentic_one.shared.config import AuthConfig, SigningKeyConfig

TEST_KEY_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgg+NyrINDm09AtfDc\n"
    "E1rrXEDFFRHyQvFRTaGyLgOTHDShRANCAAQzqcdkuHnRKQqFTPhCK7Xeg8YE+3AO\n"
    "kaHwF9Pzjob5U+FdMegYHUc+K991gdxu2k1sdbXtG7dw5RHStrJveVDK\n"
    "-----END PRIVATE KEY-----\n"
)


@pytest.fixture()
def auth_config() -> AuthConfig:
    return AuthConfig(
        canonical_base_url="https://auth.example.com",
        id_signing=[
            SigningKeyConfig(kid="key-1", private_key_pem=SecretStr(TEST_KEY_PEM)),
        ],
    )


@pytest.fixture()
def multi_key_config() -> AuthConfig:
    return AuthConfig(
        canonical_base_url="https://auth.example.com",
        id_signing=[
            SigningKeyConfig(kid="key-1", private_key_pem=SecretStr(TEST_KEY_PEM)),
            SigningKeyConfig(kid="key-2", private_key_pem=SecretStr(TEST_KEY_PEM)),
        ],
    )


def _decode_with_jwks(token: str, config: AuthConfig, audience: str) -> dict[str, object]:
    jwks_data = CachedJWKSPublisher(config).get_jwks()
    pub_key = jwt.algorithms.ECAlgorithm(jwt.algorithms.ECAlgorithm.SHA256).from_jwk(
        jwks_data["keys"][0]
    )
    return jwt.decode(token, pub_key, algorithms=["ES256"], audience=audience)  # type: ignore[arg-type]


def test_issue_id_token_valid_es256(auth_config: AuthConfig) -> None:
    token = issue_id_token(
        auth_config,
        issuer="https://auth.example.com",
        sub="usr_abc123",
        email="user@example.com",
        aud="my-client",
    )
    claims = _decode_with_jwks(token, auth_config, "my-client")
    assert claims["sub"] == "usr_abc123"
    assert claims["email"] == "user@example.com"
    assert claims["iss"] == "https://auth.example.com"
    assert claims["aud"] == "my-client"


def test_issue_id_token_includes_nonce(auth_config: AuthConfig) -> None:
    token = issue_id_token(
        auth_config,
        issuer="https://auth.example.com",
        sub="usr_abc",
        email="u@e.com",
        aud="client",
        nonce="test-nonce",
    )
    claims = _decode_with_jwks(token, auth_config, "client")
    assert claims["nonce"] == "test-nonce"


def test_issue_id_token_omits_nonce_when_none(auth_config: AuthConfig) -> None:
    token = issue_id_token(
        auth_config, issuer="https://auth.example.com", sub="usr_abc", email="u@e.com", aud="client"
    )
    claims = _decode_with_jwks(token, auth_config, "client")
    assert "nonce" not in claims


def test_issue_id_token_kid_header(auth_config: AuthConfig) -> None:
    token = issue_id_token(
        auth_config, issuer="https://auth.example.com", sub="usr_abc", email="u@e.com", aud="client"
    )
    headers = jwt.get_unverified_header(token)
    assert headers["kid"] == "key-1"
    assert headers["alg"] == "ES256"


def test_issue_id_token_raises_without_keys() -> None:
    config = AuthConfig(canonical_base_url="https://auth.example.com")
    with pytest.raises(ValueError, match="No signing keys configured"):
        issue_id_token(
            config,
            issuer="https://auth.example.com",
            sub="usr_abc",
            email="u@e.com",
            aud="client",
        )


def test_issue_id_token_has_iat_and_exp(auth_config: AuthConfig) -> None:
    token = issue_id_token(
        auth_config, issuer="https://auth.example.com", sub="usr_abc", email="u@e.com", aud="client"
    )
    claims = _decode_with_jwks(token, auth_config, "client")
    assert "iat" in claims
    assert "exp" in claims
    assert int(str(claims["exp"])) > int(str(claims["iat"]))


def test_get_active_kid_returns_first(auth_config: AuthConfig) -> None:
    assert get_active_kid(auth_config) == "key-1"


def test_get_active_kid_returns_none_without_keys() -> None:
    config = AuthConfig()
    assert get_active_kid(config) is None
