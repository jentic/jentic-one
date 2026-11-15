"""Integration tests for JWT-bearer assertion audience binding."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Iterator

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jwt.algorithms import OKPAlgorithm
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.assertion_jtis import AssertionJti
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.config import bind_origin
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.integration.auth.seeds import seed_agent, seed_user

pytestmark = pytest.mark.integration

_OWNER = "usr_aud_binding_owner"


@pytest.fixture()
async def clean_jtis(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(AssertionJti))

    await _clean()
    yield
    await _clean()


@pytest.fixture()
def restore_base_urls(integration_context: Context) -> Iterator[None]:
    auth_cfg = integration_context.config.auth
    server_cfg = integration_context.config.server
    prior_canonical = auth_cfg.canonical_base_url
    prior_public = server_cfg.public_base_url
    auth_cfg.canonical_base_url = ""
    server_cfg.public_base_url = ""
    yield
    auth_cfg.canonical_base_url = prior_canonical
    server_cfg.public_base_url = prior_public


def _make_app(ctx: Context) -> FastAPI:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    return app


async def _active_agent(ctx: Context) -> tuple[str, Ed25519PrivateKey]:
    await seed_user(ctx, _OWNER)
    key = Ed25519PrivateKey.generate()
    jwk = OKPAlgorithm().to_jwk(key.public_key())
    jwk_dict = json.loads(jwk) if isinstance(jwk, str) else dict(jwk)
    jwk_dict["kid"] = "k1"
    jwks = {"keys": [jwk_dict]}
    agent_id = await seed_agent(ctx, owner_id=_OWNER, scopes=[], status=ActorStatus.ACTIVE)
    async with ctx.admin_db.transaction() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(jwks=jwks))
    return agent_id, key


def _sign_assertion(key: Ed25519PrivateKey, agent_id: str, *, aud: str, jti: str) -> str:
    now = time.time()
    return jwt.encode(
        {
            "iss": agent_id,
            "aud": aud,
            "exp": now + 60,
            "iat": now,
            "jti": jti,
        },
        key,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )


async def test_production_unconfigured_base_url_rejects_host_derived_audience(
    integration_context: Context,
    clean_jtis: None,
    restore_base_urls: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JENTIC_ENV", "production")
    agent_id, key = await _active_agent(integration_context)
    assertion = _sign_assertion(
        key, agent_id, aud="https://evil.example/oauth/token", jti="jti-prod-host"
    )
    app = _make_app(integration_context)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://evil.example"
    ) as client:
        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            headers={"Host": "evil.example"},
        )

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


async def test_production_unconfigured_base_url_accepts_loopback_bind_origin(
    integration_context: Context,
    clean_jtis: None,
    restore_base_urls: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JENTIC_ENV", "production")
    agent_id, key = await _active_agent(integration_context)
    origin = bind_origin(integration_context.config).rstrip("/")
    assertion = _sign_assertion(key, agent_id, aud=f"{origin}/oauth/token", jti="jti-prod-loopback")
    app = _make_app(integration_context)
    port = integration_context.config.server.port
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"http://localhost:{port}"
    ) as client:
        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["access_token"].startswith("at_")


async def test_configured_public_base_url_accepts_only_configured_audience(
    integration_context: Context,
    clean_jtis: None,
    restore_base_urls: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JENTIC_ENV", "production")
    integration_context.config.server.public_base_url = "https://auth.prod.example"
    agent_id, key = await _active_agent(integration_context)
    app = _make_app(integration_context)

    bad_assertion = _sign_assertion(
        key, agent_id, aud="https://other.example/oauth/token", jti="jti-configured-bad"
    )
    good_assertion = _sign_assertion(
        key, agent_id, aud="https://auth.prod.example/oauth/token", jti="jti-configured-good"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://other.example"
    ) as client:
        bad_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": bad_assertion,
            },
            headers={"Host": "other.example"},
        )
        good_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": good_assertion,
            },
            headers={"Host": "other.example"},
        )

    assert bad_resp.status_code == 400
    assert bad_resp.json()["error"] == "invalid_grant"
    assert good_resp.status_code == 200
    assert good_resp.json()["access_token"].startswith("at_")
