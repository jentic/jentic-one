"""Unit tests for the hardened trusted-issuer JWT verifier (§08 E1).

Key resolution is stubbed with an in-memory fake (no network): each test builds
an EC/RSA key, signs a token, and points the issuer's JWKS client at the public
key. Covers the alg allowlist (none/HMAC reject), issuer/audience/expiry checks,
clock-skew leeway, and key rotation on an unknown ``kid``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import SecretStr

from jentic_one.broker.core.setup import build_jwt_verifier
from jentic_one.broker.services.auth import JwtVerifier
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.jwt_verification import (
    JwtVerificationError,
    TrustedIssuerVerifier,
)
from jentic_one.shared.config import BrokerConfig, JwtVerificationConfig, TrustedIssuerConfig

_ISS = "https://idp.example.com"
_AUD = "jentic-broker"


@dataclass
class _FakeSigningKey:
    key: Any


class _FakeJWKSClient:
    """In-memory stand-in for PyJWKClient: resolves a token's kid to a public key."""

    def __init__(self, keys_by_kid: dict[str, Any]) -> None:
        self._keys = keys_by_kid

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self._keys:
            raise jwt.PyJWKClientError(f"no key for kid {kid!r}")
        return _FakeSigningKey(self._keys[kid])


def _verifier(keys_by_kid: dict[str, Any], *, audience: str | None = _AUD) -> TrustedIssuerVerifier:
    cfg = JwtVerificationConfig(
        audience=audience,
        leeway_s=10.0,
        trusted_issuers=[
            TrustedIssuerConfig(issuer=_ISS, jwks_url="https://idp.example.com/jwks"),
        ],
    )
    v = TrustedIssuerVerifier(cfg)
    # Swap the real PyJWKClient for the in-memory fake (no network).
    v._issuers[_ISS].client = _FakeJWKSClient(keys_by_kid)  # type: ignore[assignment]
    return v


def _ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _sign(
    key: Any,
    *,
    alg: str = "ES256",
    kid: str = "k1",
    iss: str = _ISS,
    aud: str | None = _AUD,
    exp_delta: int = 3600,
    nbf_delta: int | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"iss": iss, "sub": "user-1", "exp": now + exp_delta}
    if aud is not None:
        claims["aud"] = aud
    if nbf_delta is not None:
        claims["nbf"] = now + nbf_delta
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


# --- happy paths --------------------------------------------------------------


def test_valid_es256_token_verifies() -> None:
    key = _ec_key()
    token = _sign(key)
    claims = _verifier({"k1": key.public_key()}).verify(token)
    assert claims["sub"] == "user-1"
    assert claims["iss"] == _ISS


def test_valid_rs256_token_verifies() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = JwtVerificationConfig(
        audience=_AUD,
        trusted_issuers=[
            TrustedIssuerConfig(
                issuer=_ISS, jwks_url="https://idp.example.com/jwks", algorithms=["RS256"]
            )
        ],
    )
    v = TrustedIssuerVerifier(cfg)
    v._issuers[_ISS].client = _FakeJWKSClient({"k1": key.public_key()})  # type: ignore[assignment]
    token = _sign(key, alg="RS256")
    assert v.verify(token)["sub"] == "user-1"


def test_no_audience_configured_skips_aud_check() -> None:
    key = _ec_key()
    token = _sign(key, aud=None)
    claims = _verifier({"k1": key.public_key()}, audience=None).verify(token)
    assert claims["sub"] == "user-1"


# --- alg allowlist ------------------------------------------------------------


def test_alg_none_rejected() -> None:
    # An unsigned token ("alg":"none") must never be trusted.
    token = jwt.encode(
        {"iss": _ISS, "sub": "x", "exp": int(time.time()) + 60},
        key="",
        algorithm="none",
    )
    with pytest.raises(JwtVerificationError, match="jwt_alg_not_allowed"):
        _verifier({"k1": _ec_key().public_key()}).verify(token)


def test_hmac_alg_rejected() -> None:
    # RS<->HS confusion: an HS256 token signed with the public key as the secret
    # must be rejected on the alg check, before any key lookup.
    token = jwt.encode(
        {"iss": _ISS, "aud": _AUD, "sub": "x", "exp": int(time.time()) + 60},
        "shared-secret",
        algorithm="HS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(JwtVerificationError, match="jwt_alg_not_allowed"):
        _verifier({"k1": _ec_key().public_key()}).verify(token)


def test_alg_not_in_issuer_allowlist_rejected() -> None:
    # ES384 is asymmetric but not in this issuer's [RS256, ES256] allowlist.
    key = ec.generate_private_key(ec.SECP384R1())
    token = _sign(key, alg="ES384")
    with pytest.raises(JwtVerificationError, match="jwt_alg_not_allowed"):
        _verifier({"k1": key.public_key()}).verify(token)


# --- claim checks -------------------------------------------------------------


def test_untrusted_issuer_rejected() -> None:
    key = _ec_key()
    token = _sign(key, iss="https://evil.example.com")
    with pytest.raises(JwtVerificationError, match="jwt_untrusted_issuer"):
        _verifier({"k1": key.public_key()}).verify(token)


def test_wrong_audience_rejected() -> None:
    key = _ec_key()
    token = _sign(key, aud="some-other-service")
    with pytest.raises(JwtVerificationError, match="jwt_invalid"):
        _verifier({"k1": key.public_key()}).verify(token)


def test_expired_token_rejected() -> None:
    key = _ec_key()
    token = _sign(key, exp_delta=-3600)
    with pytest.raises(JwtVerificationError, match="jwt_invalid"):
        _verifier({"k1": key.public_key()}).verify(token)


def test_expiry_within_leeway_accepted() -> None:
    key = _ec_key()
    # Expired 5s ago, but leeway is 10s.
    token = _sign(key, exp_delta=-5)
    assert _verifier({"k1": key.public_key()}).verify(token)["sub"] == "user-1"


def test_not_yet_valid_rejected() -> None:
    key = _ec_key()
    token = _sign(key, nbf_delta=3600)
    with pytest.raises(JwtVerificationError, match="jwt_invalid"):
        _verifier({"k1": key.public_key()}).verify(token)


# --- key resolution / rotation ------------------------------------------------


def test_unknown_kid_rejected() -> None:
    key = _ec_key()
    token = _sign(key, kid="missing")
    with pytest.raises(JwtVerificationError, match="jwt_key_unresolved"):
        _verifier({"k1": key.public_key()}).verify(token)


def test_rotated_key_picked_up() -> None:
    # A token signed by a freshly-rotated key (new kid) verifies once the JWKS
    # client has the new key — PyJWKClient re-fetches on an unknown kid in prod.
    old, new = _ec_key(), _ec_key()
    keys = {"k1": old.public_key()}
    v = _verifier(keys)
    rotated = _sign(new, kid="k2")
    # Before the client learns the new key, it can't resolve it.
    with pytest.raises(JwtVerificationError, match="jwt_key_unresolved"):
        v.verify(rotated)
    # After rotation (the fetch that PyJWKClient does on an unknown kid):
    keys["k2"] = new.public_key()
    assert v.verify(rotated)["sub"] == "user-1"


# --- config-driven verifier selection -----------------------------------------


def test_build_verifier_prefers_trusted_issuers() -> None:
    broker = BrokerConfig(
        jwt_secret=None,
        jwt_verification=JwtVerificationConfig(
            trusted_issuers=[
                TrustedIssuerConfig(issuer=_ISS, jwks_url="https://idp.example.com/jwks")
            ],
        ),
    )
    assert isinstance(build_jwt_verifier(broker), TrustedIssuerVerifier)


def test_build_verifier_falls_back_to_hs256_secret() -> None:
    broker = BrokerConfig(jwt_secret=SecretStr("dev-secret"))
    assert isinstance(build_jwt_verifier(broker), JwtVerifier)


def test_build_verifier_disabled_when_unconfigured() -> None:
    assert build_jwt_verifier(BrokerConfig(jwt_secret=None)) is None


def test_jwt_verification_error_is_token_validation_error() -> None:
    """The broker edge catches every refusal as one typed base (jentic-one#866)."""
    assert issubclass(JwtVerificationError, TokenValidationError)
