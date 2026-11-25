"""Unit tests for JWKS endpoint with populated keys."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jentic_one.auth.web.routers import discovery
from jentic_one.shared.config import AuthConfig, SigningKeyConfig

TEST_KEY_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgg+NyrINDm09AtfDc\n"
    "E1rrXEDFFRHyQvFRTaGyLgOTHDShRANCAAQzqcdkuHnRKQqFTPhCK7Xeg8YE+3AO\n"
    "kaHwF9Pzjob5U+FdMegYHUc+K991gdxu2k1sdbXtG7dw5RHStrJveVDK\n"
    "-----END PRIVATE KEY-----\n"
)


@pytest.fixture()
def client_with_keys() -> TestClient:
    app = FastAPI()
    app.include_router(discovery.router)
    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        id_signing=[
            SigningKeyConfig(kid="key-1", private_key_pem=SecretStr(TEST_KEY_PEM)),
        ],
    )
    app.state.ctx = mock_ctx
    return TestClient(app)


@pytest.fixture()
def client_no_keys() -> TestClient:
    app = FastAPI()
    app.include_router(discovery.router)
    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    app.state.ctx = mock_ctx
    return TestClient(app)


def test_jwks_returns_configured_key(client_with_keys: TestClient) -> None:
    resp = client_with_keys.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["keys"]) == 1
    assert data["keys"][0]["kid"] == "key-1"
    assert data["keys"][0]["alg"] == "ES256"
    assert data["keys"][0]["kty"] == "EC"
    assert data["keys"][0]["crv"] == "P-256"


def test_jwks_empty_when_no_keys(client_no_keys: TestClient) -> None:
    resp = client_no_keys.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}


def test_discovery_includes_authorization_endpoint(client_with_keys: TestClient) -> None:
    resp = client_with_keys.get("/.well-known/oauth-authorization-server")
    data = resp.json()
    assert data["authorization_endpoint"] == "https://auth.example.com/authorize"
    assert "authorization_code" in data["grant_types_supported"]
    assert "code" in data["response_types_supported"]
    assert "S256" in data["code_challenge_methods_supported"]
    assert "ES256" in data["id_token_signing_alg_values_supported"]


def _generate_es256_pem() -> str:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(
        "utf-8"
    )


def test_jwks_publisher_cache_keys_use_sha256_digest_not_raw_pem() -> None:
    """Module-global ``_jwks_publishers`` keys store SHA-256 digests and never raw PEM strings."""
    discovery._jwks_publishers.clear()
    try:
        cfg = AuthConfig(
            canonical_base_url="https://auth.example.com",
            id_signing=[
                SigningKeyConfig(kid="key-digest-1", private_key_pem=SecretStr(TEST_KEY_PEM)),
            ],
        )
        pub_first = discovery._get_publisher(cfg)
        pub_second = discovery._get_publisher(cfg)
        assert pub_first is pub_second

        expected_digest = hashlib.sha256(TEST_KEY_PEM.encode("utf-8")).hexdigest()
        assert list(discovery._jwks_publishers.keys()) == [(("key-digest-1", expected_digest),)]

        pem_body_lines = [
            line.strip()
            for line in TEST_KEY_PEM.splitlines()
            if line.strip() and not line.startswith("-----")
        ]
        assert pem_body_lines
        serialized_keys = repr(list(discovery._jwks_publishers.keys()))
        assert "PRIVATE KEY" not in serialized_keys
        for line in pem_body_lines:
            assert line not in serialized_keys
    finally:
        discovery._jwks_publishers.clear()


def test_jwks_publisher_cache_bounds_size_with_lru_eviction() -> None:
    """``_jwks_publishers`` evicts least-recently-used entries above ``_MAX_JWKS_PUBLISHERS``."""
    discovery._jwks_publishers.clear()
    try:
        configs = [
            AuthConfig(
                canonical_base_url="https://auth.example.com",
                id_signing=[
                    SigningKeyConfig(
                        kid=f"rot-{idx}",
                        private_key_pem=SecretStr(_generate_es256_pem()),
                    )
                ],
            )
            for idx in range(discovery._MAX_JWKS_PUBLISHERS + 3)
        ]

        # Populate exactly up to the bound.
        for cfg in configs[: discovery._MAX_JWKS_PUBLISHERS]:
            discovery._get_publisher(cfg)
        assert len(discovery._jwks_publishers) == discovery._MAX_JWKS_PUBLISHERS

        # Touch rot-0 so it moves to the MRU end; rot-1 becomes LRU.
        first_pub = discovery._get_publisher(configs[0])

        # Insert 3 more rotated configs; bound stays capped and rot-1..rot-3 are evicted.
        for cfg in configs[discovery._MAX_JWKS_PUBLISHERS :]:
            discovery._get_publisher(cfg)

        assert len(discovery._jwks_publishers) == discovery._MAX_JWKS_PUBLISHERS
        assert discovery._publisher_key(configs[0]) in discovery._jwks_publishers
        assert discovery._get_publisher(configs[0]) is first_pub
        for evicted_cfg in configs[1:4]:
            assert discovery._publisher_key(evicted_cfg) not in discovery._jwks_publishers
    finally:
        discovery._jwks_publishers.clear()
