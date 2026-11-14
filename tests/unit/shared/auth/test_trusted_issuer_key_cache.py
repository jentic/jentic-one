"""Unit tests for TrustedIssuerVerifier JWKS key caching, withdrawal, and async fetch."""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm

from jentic_one.broker.services.auth.token_validation import JwtTokenValidator
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.jwt_verification import (
    JwtVerificationError,
    TrustedIssuerVerifier,
)
from jentic_one.shared.config import JwtVerificationConfig, TrustedIssuerConfig

_ISS = "https://idp.example.test"
_AUD = "jentic-broker"


def _ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _to_jwk(key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, Any]:
    jwk: dict[str, Any] = ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return jwk | {"kid": kid, "use": "sig", "alg": "ES256"}


def _mint(
    key: ec.EllipticCurvePrivateKey,
    *,
    kid: str | None = "k1",
    iss: str = _ISS,
    aud: str = _AUD,
    sub: str = "agnt_x",
    headers: dict[str, Any] | None = None,
) -> str:
    hdrs: dict[str, Any] = {} if headers is not None else ({"kid": kid} if kid is not None else {})
    if headers is not None:
        hdrs = dict(headers)
    return jwt.encode(
        {
            "iss": iss,
            "sub": sub,
            "aud": aud,
            "exp": int(time.time()) + 600,
            "actor_type": "agent",
        },
        key,
        algorithm="ES256",
        headers=hdrs,
    )


def _make_verifier() -> TrustedIssuerVerifier:
    return TrustedIssuerVerifier(
        JwtVerificationConfig(
            audience=_AUD,
            trusted_issuers=[
                TrustedIssuerConfig(
                    issuer=_ISS,
                    jwks_url=f"{_ISS}/jwks",
                    algorithms=["ES256"],
                )
            ],
        )
    )


@pytest.mark.asyncio
async def test_withdrawn_kid_refused_after_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key removed from the issuer JWKS stops verifying once the JWK-set cache expires."""
    old_key = _ec_key()
    new_key = _ec_key()
    published: dict[str, Any] = {"keys": [_to_jwk(old_key, "k-old")]}
    fetches = 0

    def fetch(self: PyJWKClient) -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        return published

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)
    verifier = _make_verifier()
    token = _mint(old_key, kid="k-old")

    claims = await verifier.verify(token)
    assert claims["sub"] == "agnt_x"
    assert fetches == 1

    # The issuer rotates keys and withdraws k-old; the JWK-set cache expires after lifespan.
    published = {"keys": [_to_jwk(new_key, "k-new")]}
    for entry in verifier._issuers.values():
        assert entry.client.jwk_set_cache is not None
        entry.client.jwk_set_cache.put(None)

    with pytest.raises(JwtVerificationError, match="jwt_key_unresolved"):
        await verifier.verify(token)
    assert fetches == 2


@pytest.mark.asyncio
async def test_known_kid_hit_does_no_fetch_and_no_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warm JWK-set cache resolves a known kid inline without I/O or thread offload."""
    key = _ec_key()
    published: dict[str, Any] = {"keys": [_to_jwk(key, "k1")]}
    fetches = 0

    def fetch(self: PyJWKClient) -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        return published

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)
    verifier = _make_verifier()
    token = _mint(key, kid="k1")

    # First verification warms the JWK-set cache via the slow path.
    first = await verifier.verify(token)
    assert first["sub"] == "agnt_x"
    assert fetches == 1

    async def fail_to_thread(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("warm cache hit must not hop to a worker thread")

    monkeypatch.setattr(asyncio, "to_thread", fail_to_thread)

    second = await verifier.verify(token)
    third = await verifier.verify(token)
    assert second["sub"] == "agnt_x"
    assert third["sub"] == "agnt_x"
    assert fetches == 1


@pytest.mark.asyncio
async def test_jwks_fetch_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking JWKS fetch runs off the event loop so concurrent coroutines keep ticking."""
    known_key = _ec_key()
    unknown_key = _ec_key()
    published: dict[str, Any] = {"keys": [_to_jwk(known_key, "k-known")]}
    fetches = 0

    def slow_fetch(self: PyJWKClient) -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        time.sleep(0.5)
        return published

    monkeypatch.setattr(PyJWKClient, "fetch_data", slow_fetch)
    validator = JwtTokenValidator(verifier=_make_verifier())
    token = _mint(unknown_key, kid="unknown-kid")

    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    ticker_task = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    try:
        with pytest.raises(TokenValidationError, match="jwt_key_unresolved"):
            await validator.validate(token)
    finally:
        stop.set()
        await ticker_task

    assert fetches == 1
    assert len(ticks) >= 10
    max_gap = max(b - a for a, b in itertools.pairwise(ticks))
    assert max_gap < 0.05, f"event loop stalled for {max_gap:.3f}s during JWKS fetch"


@pytest.mark.asyncio
async def test_concurrent_unknown_kids_coalesce_to_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent misses for an unknown kid coalesce under the per-issuer lock to <=1 fetch."""
    known_key = _ec_key()
    unknown_key = _ec_key()
    published: dict[str, Any] = {"keys": [_to_jwk(known_key, "k-known")]}
    fetches = 0

    def fetch(self: PyJWKClient) -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        time.sleep(0.05)
        return published

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)
    verifier = _make_verifier()
    token = _mint(unknown_key, kid="k-unknown")

    results = await asyncio.gather(
        *(verifier.verify(token) for _ in range(20)),
        return_exceptions=True,
    )

    assert len(results) == 20
    for res in results:
        assert isinstance(res, JwtVerificationError)
        assert "jwt_key_unresolved" in str(res)
    assert fetches <= 1


@pytest.mark.asyncio
async def test_missing_kid_refused_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token missing a valid string kid is refused before any JWKS lookup."""
    key = _ec_key()
    fetches = 0

    def fetch(self: PyJWKClient) -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        raise AssertionError("JWKS fetch must not run when kid is missing")

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)
    verifier = _make_verifier()

    no_kid_token = _mint(key, kid=None)
    with pytest.raises(JwtVerificationError, match="jwt_kid_missing"):
        await verifier.verify(no_kid_token)

    empty_kid_token = _mint(key, headers={"kid": ""})
    with pytest.raises(JwtVerificationError, match="jwt_kid_missing"):
        await verifier.verify(empty_kid_token)

    assert fetches == 0


@pytest.mark.asyncio
async def test_fetch_timeout_maps_to_uniform_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JWKS fetch timeout maps to jwt_key_unresolved and TokenValidationError."""
    key = _ec_key()

    def timed_out_fetch(self: PyJWKClient) -> dict[str, Any]:
        raise jwt.PyJWKClientError("timed out")

    monkeypatch.setattr(PyJWKClient, "fetch_data", timed_out_fetch)
    verifier = _make_verifier()
    assert verifier._issuers[_ISS].client.timeout == 5.0

    token = _mint(key, kid="k1")
    with pytest.raises(JwtVerificationError, match="jwt_key_unresolved"):
        await verifier.verify(token)

    validator = JwtTokenValidator(verifier=verifier)
    with pytest.raises(TokenValidationError, match="jwt_key_unresolved"):
        await validator.validate(token)
