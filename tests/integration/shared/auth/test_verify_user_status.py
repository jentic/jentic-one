"""Integration tests: a login JWT dies with its user (#1139).

``verify_token`` re-checks the ``users`` row on every verification, so
disabling (or deleting) a user ends their outstanding login JWT on the very
next request instead of at ``admin.auth.jwt_ttl_seconds``. Every app shape the
composition root (``jentic_one.__main__._build_app``) serves a login JWT on is
covered: the combined app, each standalone surface that installs a token
verifier, and the ``/mcp`` mount (which delegates to the same app-state
verifier). The broker never accepts a user JWT (its JWT path admits only
``actor_type: agent``), so it has no login-JWT surface to cover here.

Login, disable, enable and delete all go through the real services; requests
go through the real app built by the composition root.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select

from jentic_one.__main__ import _build_app
from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    AgentRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services import AuthService, UserService
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services.schemas.auth import LoginPayload
from jentic_one.auth.services.token_service import TokenService
from jentic_one.mcp.installer import mcp_lifespan
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_jwt_status_seed"
_PASSWORD = "jwt-status-password-123"  # pragma: allowlist secret
_ADMIN_EMAIL = "jwt-status-admin@test.local"
_USER_EMAIL = "jwt-status-user@test.local"
#: What the target user's login JWT carries — enough to pass every probe route.
_USER_PERMISSIONS = {"users:read", "apis:read", "credentials:read"}

_FULL = ["registry", "admin", "control", "auth"]


@dataclass(frozen=True)
class _Probe:
    """An authenticated route on an app shape that a plain user can call."""

    apps: tuple[str, ...]
    path: str


#: One probe per (shape, surface) pair that verifies a login JWT. The combined
#: app is probed on every surface's route, since its verifier is the auth
#: surface's superset verifier regardless of which surface owns the route.
_PROBES = [
    _Probe(tuple(_FULL), "/users/me"),
    _Probe(tuple(_FULL), "/me"),
    _Probe(tuple(_FULL), "/apis"),
    _Probe(tuple(_FULL), "/credentials"),
    _Probe(("admin",), "/users/me"),
    _Probe(("auth",), "/me"),
    _Probe(("registry",), "/apis"),
    _Probe(("control",), "/credentials"),
]


@dataclass(frozen=True)
class _Actors:
    admin_id: str
    user_id: str


@pytest.fixture()
async def actors(integration_context: Context) -> AsyncGenerator[_Actors, None]:
    """An org:admin operator plus a password user with read permissions."""
    ctx = integration_context

    async def _cleanup() -> None:
        async with ctx.admin_db.session() as session:
            seeded = (User.created_by == _SEED_MARKER) | User.email.in_([_ADMIN_EMAIL, _USER_EMAIL])
            ids = list((await session.execute(select(User.id).where(seeded))).scalars())
            agents = select(Agent.id).where(Agent.created_by == _SEED_MARKER)
            await session.execute(delete(AccessToken).where(AccessToken.actor_id.in_(agents)))
            await session.execute(delete(RefreshToken).where(RefreshToken.actor_id.in_(agents)))
            await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id.in_(ids))
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id.in_(ids)))
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.commit()

    await _cleanup()
    ids: dict[str, str] = {}
    async with ctx.admin_db.session() as session:
        for key, email, perms in (
            ("admin", _ADMIN_EMAIL, {"org:admin"}),
            ("user", _USER_EMAIL, _USER_PERMISSIONS),
        ):
            user = await UserRepository.create(
                session,
                email=email,
                first_name="Jwt",
                last_name=key.title(),
                invite_state=InviteState.REDEEMED,
                created_by=_SEED_MARKER,
            )
            await UserSecretRepository.create(
                session,
                user_id=user.id,
                password_hash=hash_password(_PASSWORD),
                created_by=_SEED_MARKER,
            )
            await UserPermissionGrantRepository.set_permissions(
                session, user.id, permissions=perms, granted_by=None, created_by=_SEED_MARKER
            )
            ids[key] = user.id
        await session.commit()
    yield _Actors(admin_id=ids["admin"], user_id=ids["user"])
    await _cleanup()


def _operator(actors: _Actors) -> Identity:
    return Identity(sub=actors.admin_id, email=_ADMIN_EMAIL, permissions=["org:admin"])


async def _login(ctx: Context) -> str:
    bundle = await AuthService(ctx).login(LoginPayload(email=_USER_EMAIL, password=_PASSWORD))
    return bundle.access_token


@asynccontextmanager
async def _client(ctx: Context, apps: list[str]) -> AsyncIterator[AsyncClient]:
    """The app the composition root serves for ``apps``, over the real ASGI stack."""
    app: FastAPI = _build_app(ctx, apps)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _get(client: AsyncClient, path: str, token: str) -> Response:
    return await client.get(path, headers={"Authorization": f"Bearer {token}"})


def _assert_refused(resp: Response) -> None:
    """The uniform invalid-token 401 — no new problem type, no oracle."""
    assert resp.status_code == 401, resp.text
    assert resp.json()["type"] == "unauthorized"


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: f"{'+'.join(p.apps)}:{p.path}")
async def test_disabled_user_jwt_refused_then_revived_on_enable(
    integration_context: Context, actors: _Actors, probe: _Probe
) -> None:
    """Login → disable → the unexpired JWT is refused; enable → the same JWT works again."""
    ctx = integration_context
    token = await _login(ctx)
    users = UserService(ctx)

    async with _client(ctx, list(probe.apps)) as client:
        assert (await _get(client, probe.path, token)).status_code == 200

        await users.disable(actors.user_id, identity=_operator(actors))
        _assert_refused(await _get(client, probe.path, token))

        await users.enable(actors.user_id, identity=_operator(actors))
        assert (await _get(client, probe.path, token)).status_code == 200


@pytest.mark.parametrize("apps", [_FULL, ["admin"], ["control"]], ids="+".join)
async def test_deleted_user_jwt_refused(
    integration_context: Context, actors: _Actors, apps: list[str]
) -> None:
    """A deleted user's unexpired JWT is refused — the soft delete and a vanished row alike."""
    ctx = integration_context
    token = await _login(ctx)
    path = "/credentials" if apps == ["control"] else "/users/me"

    async with _client(ctx, apps) as client:
        assert (await _get(client, path, token)).status_code == 200

        await UserService(ctx).delete(actors.user_id, identity=_operator(actors))
        _assert_refused(await _get(client, path, token))

        # A hard-removed row (no users row at all) fails closed the same way.
        async with ctx.admin_db.session() as session:
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == actors.user_id)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == actors.user_id))
            await session.execute(delete(User).where(User.id == actors.user_id))
            await session.commit()
        _assert_refused(await _get(client, path, token))


async def test_disabled_user_jwt_refused_on_mcp_mount(
    integration_context: Context, actors: _Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``/mcp`` mount resolves bearers through the same verifier, so it refuses too."""
    ctx = integration_context
    monkeypatch.setattr(ctx.config.server.mcp, "enabled", True)
    token = await _login(ctx)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    app = _build_app(ctx, _FULL)
    async with (
        mcp_lifespan(app, ctx),
        AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client,
    ):
        assert (await client.post("/mcp", json=body, headers=headers)).status_code == 200

        await UserService(ctx).disable(actors.user_id, identity=_operator(actors))
        resp = await client.post("/mcp", json=body, headers=headers)
        assert resp.status_code == 401, resp.text
        assert resp.headers["www-authenticate"].startswith("Bearer")


async def test_agent_token_unaffected_by_user_status_check(
    integration_context: Context, actors: _Actors
) -> None:
    """Agent credentials never hit the users-row check: an agent's ``at_`` token keeps
    working while a user is disabled, and its subject is not a ``users`` row."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="jwt-status-agent",
            owner_id=actors.admin_id,
            registered_by=actors.admin_id,
            created_by=_SEED_MARKER,
            status=ActorStatus.ACTIVE,
        )
        await session.commit()
    access, _refresh = await TokenService(ctx).issue_pair(agent.id, ActorType.AGENT, [])
    user_token = await _login(ctx)

    async with _client(ctx, _FULL) as client:
        assert (await _get(client, "/me", access)).status_code == 200

        await UserService(ctx).disable(actors.user_id, identity=_operator(actors))
        _assert_refused(await _get(client, "/me", user_token))
        resp = await _get(client, "/me", access)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == agent.id
