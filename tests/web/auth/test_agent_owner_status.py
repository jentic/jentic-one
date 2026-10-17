"""Owned agents follow their owner's account status, end to end over HTTP.

The owner is disabled, re-enabled and deleted through the real
``UserService``; the agent then calls ``GET /me`` on the auth app with each
credential it can hold. The app's ``verify_token`` is the superset verifier
the REST surfaces and the ``/mcp`` mount share, so a refusal here is the
refusal every one of them returns.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.repos import AgentCredentialRepository, AgentRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration


def _login_jwt(ctx: Context, user_id: str, permissions: list[str]) -> str:
    """A user's login JWT, as the admin login route mints it."""
    config = ctx.config.admin.auth
    claims = {"sub": user_id, "actor_type": "user", "permissions": permissions}
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


@dataclass(frozen=True)
class _AgentCredentials:
    agent_id: str
    api_key: str
    access_token: str


async def _seed_agent(ctx: Context, *, owner_id: str | None, name: str) -> _AgentCredentials:
    """An active agent holding a ``jak_`` key and an ``at_`` access token."""
    api_key = f"jak_{secrets.token_hex(16)}"
    async with ctx.admin_db.transaction() as session:
        agent = Agent(
            name=name,
            owner_id=owner_id,
            registered_by=owner_id or "system:test",
            created_by="usr_test",
            status=ActorStatus.ACTIVE,
        )
        session.add(agent)
        await session.flush()
        await AgentCredentialRepository.create(
            session,
            agent_id=agent.id,
            api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
            created_by="usr_test",
        )
        agent_id = agent.id
    access_token, _refresh = await TokenService(ctx).issue_pair(agent_id, ActorType.AGENT, [])
    return _AgentCredentials(agent_id=agent_id, api_key=api_key, access_token=access_token)


async def _remove_agent(ctx: Context, agent_id: str) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(delete(AccessToken).where(AccessToken.actor_id == agent_id))
        await session.execute(delete(RefreshToken).where(RefreshToken.actor_id == agent_id))
        await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.commit()


@pytest.fixture()
async def owned_agent(
    web_context: Context, owner_user_id: str
) -> AsyncGenerator[_AgentCredentials, None]:
    creds = await _seed_agent(web_context, owner_id=owner_user_id, name="owned-agent")
    yield creds
    await _remove_agent(web_context, creds.agent_id)


@pytest.fixture()
async def ownerless_agent(web_context: Context) -> AsyncGenerator[_AgentCredentials, None]:
    creds = await _seed_agent(web_context, owner_id=None, name="ownerless-agent")
    yield creds
    await _remove_agent(web_context, creds.agent_id)


def _admin(admin_user_id: str) -> Identity:
    return Identity(sub=admin_user_id, permissions=["org:admin"], actor_type=ActorType.USER)


def _me_status(ctx: Context, credential: str) -> int:
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    with TestClient(app, headers={"Authorization": f"Bearer {credential}"}) as client:
        return client.get("/me").status_code


def _agent_channels(creds: _AgentCredentials) -> dict[str, str]:
    return {"jak_": creds.api_key, "at_": creds.access_token}


async def test_owned_agent_follows_owner_disable_enable_and_delete(
    web_context: Context, admin_user_id: str, owner_user_id: str, owned_agent: _AgentCredentials
) -> None:
    users = UserService(web_context)
    admin = _admin(admin_user_id)
    channels = _agent_channels(owned_agent)
    assert {name: _me_status(web_context, c) for name, c in channels.items()} == {
        "jak_": 200,
        "at_": 200,
    }

    await users.disable(owner_user_id, identity=admin)
    disabled = {name: _me_status(web_context, c) for name, c in channels.items()}

    await users.enable(owner_user_id, identity=admin)
    re_enabled = {name: _me_status(web_context, c) for name, c in channels.items()}

    await users.delete(owner_user_id, identity=admin)
    deleted = {name: _me_status(web_context, c) for name, c in channels.items()}

    assert disabled == {"jak_": 401, "at_": 401}
    assert re_enabled == {"jak_": 200, "at_": 200}
    assert deleted == {"jak_": 401, "at_": 401}
    # The agent's own status is never changed by its owner's.
    async with web_context.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, owned_agent.agent_id)
        assert agent is not None and agent.status == ActorStatus.ACTIVE


async def test_owner_inactive_refusal_matches_inactive_agent_refusal(
    web_context: Context, admin_user_id: str, owner_user_id: str, owned_agent: _AgentCredentials
) -> None:
    """The refusal is the same uniform 401 body a disabled agent gets."""

    def _bodies() -> dict[str, dict[str, object]]:
        app = create_app(web_context)
        app.router.lifespan_context = noop_lifespan
        bodies = {}
        for name, credential in _agent_channels(owned_agent).items():
            headers = {"Authorization": f"Bearer {credential}"}
            with TestClient(app, headers=headers) as client:
                resp = client.get("/me")
            assert resp.status_code == 401
            bodies[name] = resp.json()
        return bodies

    await UserService(web_context).disable(owner_user_id, identity=_admin(admin_user_id))
    owner_off = _bodies()

    await UserService(web_context).enable(owner_user_id, identity=_admin(admin_user_id))
    async with web_context.admin_db.session() as session:
        await AgentRepository.update_status(session, owned_agent.agent_id, ActorStatus.DISABLED)
        await session.commit()
    agent_off = _bodies()

    assert owner_off == agent_off


async def test_ownerless_agent_and_user_login_are_unaffected(
    web_context: Context,
    admin_user_id: str,
    owner_user_id: str,
    owned_agent: _AgentCredentials,
    ownerless_agent: _AgentCredentials,
) -> None:
    """Disabling one owner touches only that owner's agents: an ownerless
    agent and another user's own login keep working."""
    await UserService(web_context).disable(owner_user_id, identity=_admin(admin_user_id))

    for credential in _agent_channels(ownerless_agent).values():
        assert _me_status(web_context, credential) == 200
    admin_jwt = _login_jwt(web_context, admin_user_id, ["org:admin"])
    assert _me_status(web_context, admin_jwt) == 200
    admin_at, _rt = await TokenService(web_context).issue_pair(
        admin_user_id, ActorType.USER, ["openid"]
    )
    assert _me_status(web_context, admin_at) == 200

    await UserService(web_context).enable(owner_user_id, identity=_admin(admin_user_id))
    owner_jwt = _login_jwt(web_context, owner_user_id, ["agents:read"])
    assert _me_status(web_context, owner_jwt) == 200
    async with web_context.admin_db.session() as session:
        await session.execute(delete(AccessToken).where(AccessToken.actor_id == admin_user_id))
        await session.execute(delete(RefreshToken).where(RefreshToken.actor_id == admin_user_id))
        await session.commit()
