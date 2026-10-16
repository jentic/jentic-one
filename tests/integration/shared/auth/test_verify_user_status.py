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

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partialmethod
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, update

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
from jentic_one.admin.services.errors import ConflictError, InvalidCredentialsError
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.auth import LoginPayload
from jentic_one.admin.web.routers import events as events_router
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.mcp.installer import mcp_lifespan
from jentic_one.shared.auth.identity import ChangePasswordPayload, Identity
from jentic_one.shared.auth.tokens import InvalidTokenError, issue_jwt
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_jwt_status_seed"
_PASSWORD = "jwt-status-password-123"  # pragma: allowlist secret
_NEW_PASSWORD = "jwt-status-password-456"  # pragma: allowlist secret
_ADMIN_EMAIL = "jwt-status-admin@test.local"
_USER_EMAIL = "jwt-status-user@test.local"
#: What the target user's login JWT carries — enough to pass every probe route.
_USER_PERMISSIONS = {"users:read", "apis:read", "credentials:read", "events:read"}

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
            await session.execute(delete(AccessToken).where(AccessToken.actor_id.in_(ids)))
            await session.execute(delete(RefreshToken).where(RefreshToken.actor_id.in_(ids)))
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
async def test_disabled_user_jwt_refused_and_stays_refused_after_enable(
    integration_context: Context, actors: _Actors, probe: _Probe
) -> None:
    """Login → disable → the unexpired JWT is refused; enable → that JWT stays
    refused (disable revokes the session), while a fresh login works."""
    ctx = integration_context
    token = await _login(ctx)
    users = UserService(ctx)

    async with _client(ctx, list(probe.apps)) as client:
        assert (await _get(client, probe.path, token)).status_code == 200

        await users.disable(actors.user_id, identity=_operator(actors))
        _assert_refused(await _get(client, probe.path, token))

        await users.enable(actors.user_id, identity=_operator(actors))
        _assert_refused(await _get(client, probe.path, token))
        assert (await _get(client, probe.path, await _login(ctx))).status_code == 200


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

        # Delete is terminal: the tombstoned account cannot be re-enabled, so
        # its JWT can never come back that way either.
        with pytest.raises(ConflictError):
            await UserService(ctx).enable(actors.user_id, identity=_operator(actors))
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


async def test_password_change_revokes_other_sessions(
    integration_context: Context, actors: _Actors
) -> None:
    """A password change ends every session issued before it; the bundle it returns works."""
    ctx = integration_context
    earlier = await _login(ctx)
    identity = Identity(sub=actors.user_id, email=_USER_EMAIL, permissions=[])
    bundle = await AuthService(ctx).change_own_password(
        ChangePasswordPayload(current_password=_PASSWORD, new_password=_NEW_PASSWORD),
        identity=identity,
    )

    async with _client(ctx, _FULL) as client:
        _assert_refused(await _get(client, "/users/me", earlier))
        assert (await _get(client, "/users/me", bundle.access_token)).status_code == 200


async def test_password_reset_revokes_sessions(
    integration_context: Context, actors: _Actors
) -> None:
    """An operator password reset ends every session issued under the old password."""
    ctx = integration_context
    token = await _login(ctx)
    await AuthService(ctx).reset_password(email=_USER_EMAIL, temporary_password=_NEW_PASSWORD)

    async with _client(ctx, _FULL) as client:
        _assert_refused(await _get(client, "/users/me", token))


def _secret(ctx: Context) -> str:
    return ctx.config.admin.auth.jwt_secret.get_secret_value()


async def _mark_deleted_but_active(ctx: Context, user_id: str) -> None:
    """Stamp ``deleted_at`` while leaving ``active`` true and the generation unchanged.

    The row state a delete overwritten by a concurrent enable would leave: only
    the deleted marker is left to refuse the user's credentials.
    """
    async with ctx.admin_db.transaction() as session:
        await session.execute(update(User).where(User.id == user_id).values(deleted_at=func.now()))


@pytest.mark.parametrize("reason", ["missing", "deleted", "inactive", "revoked"])
async def test_refusal_logs_reason(
    integration_context: Context, actors: _Actors, reason: str
) -> None:
    """Each refusal is the same InvalidTokenError on the wire, but the server-side
    ``jwt_user_inactive`` line names why: missing row, deleted row, disabled user,
    or revoked session."""
    ctx = integration_context
    token = await _login(ctx)
    users = UserService(ctx)
    if reason == "missing":
        token = issue_jwt(
            {"sub": "usr_jwt_status_absent", "actor_type": "user", "permissions": []},
            _secret(ctx),
            300,
        )
    elif reason == "deleted":
        await _mark_deleted_but_active(ctx, actors.user_id)
    else:
        await users.disable(actors.user_id, identity=_operator(actors))
        if reason == "revoked":
            await users.enable(actors.user_id, identity=_operator(actors))

    with structlog.testing.capture_logs() as logs, pytest.raises(InvalidTokenError):
        await verify_token(token, secret=_secret(ctx), ctx=ctx)

    refusals = [e for e in logs if e["event"] == "jwt_user_inactive"]
    assert [e["reason"] for e in refusals] == [reason]


async def test_deleted_row_refuses_every_credential_even_when_active(
    integration_context: Context, actors: _Actors
) -> None:
    """``deleted_at`` alone refuses a user on every credential path: password
    login, JWT refresh and verification, and opaque ``at_``/``rt_`` tokens on the
    auth surface and the broker, even on a row that still reads active."""
    ctx = integration_context
    jwt = await _login(ctx)
    tokens = TokenService(ctx)
    access, refresh = await tokens.issue_pair(actors.user_id, ActorType.USER, [])
    resolved = await tokens.resolve_access_token(access)
    assert resolved is not None and resolved.active

    await _mark_deleted_but_active(ctx, actors.user_id)
    async with ctx.admin_db.session() as session:
        row = await UserRepository.get_by_id(session, actors.user_id)
    assert row is not None and row.active and row.deleted_at is not None

    auth = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await auth.login(LoginPayload(email=_USER_EMAIL, password=_PASSWORD))
    with pytest.raises(InvalidCredentialsError):
        await auth.refresh(jwt)
    with pytest.raises(InvalidTokenError):
        await verify_token(jwt, secret=_secret(ctx), ctx=ctx)

    resolved = await tokens.resolve_access_token(access)
    assert resolved is not None and not resolved.active
    assert (await tokens.introspect(access))["active"] is False
    assert (await tokens.introspect(refresh))["active"] is False
    with pytest.raises(InvalidGrantError):
        await tokens.refresh(refresh)

    brokered = await InProcessTokenResolver(ctx.admin_db).resolve_access_token(access)
    assert brokered is not None and not brokered.active
    assert not await ActorStatusResolver(ctx.admin_db).is_active(
        actor_id=actors.user_id, actor_type=ActorType.USER.value
    )

    async with _client(ctx, _FULL) as client:
        _assert_refused(await _get(client, "/users/me", jwt))
        _assert_refused(await _get(client, "/me", access))


@pytest.fixture()
async def unreachable_admin_ctx(
    integration_config: AppConfig, tmp_path: Path
) -> AsyncGenerator[Context, None]:
    """A Context whose admin database cannot be reached (the other databases are real)."""
    admin = integration_config.databases.admin
    if admin.backend == "sqlite":
        # A file under a directory that does not exist: SQLite cannot open it.
        unreachable = admin.model_copy(update={"path": str(tmp_path / "absent" / "admin.db")})
    else:
        # Nothing listens on port 1: the connect is refused.
        unreachable = admin.model_copy(update={"port": 1})
    databases = integration_config.databases.model_copy(update={"admin": unreachable})
    async with Context(integration_config.model_copy(update={"databases": databases})) as ctx:
        yield ctx


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: f"{'+'.join(p.apps)}:{p.path}")
async def test_admin_db_unavailable_is_503_not_401(
    unreachable_admin_ctx: Context, probe: _Probe
) -> None:
    """When the users-row read cannot run, the caller is still refused, but with a
    retryable 503 ``database_unavailable`` — never the 401 that tells a client its
    session is dead."""
    ctx = unreachable_admin_ctx
    token = issue_jwt(
        {"sub": "usr_jwt_status_any", "actor_type": "user", "permissions": ["org:admin"]},
        _secret(ctx),
        300,
    )

    with structlog.testing.capture_logs() as logs:
        async with _client(ctx, list(probe.apps)) as client:
            resp = await _get(client, probe.path, token)

    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "database_unavailable"
    assert "SELECT" not in resp.text
    assert any(e["event"] == "jwt_user_status_unavailable" for e in logs)


async def test_admin_db_unavailable_is_503_on_mcp_mount(
    unreachable_admin_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``/mcp`` gate answers 503 too, not the 401 challenge that would send the
    client off to re-authenticate."""
    ctx = unreachable_admin_ctx
    monkeypatch.setattr(ctx.config.server.mcp, "enabled", True)
    token = issue_jwt(
        {"sub": "usr_jwt_status_any", "actor_type": "user", "permissions": ["org:admin"]},
        _secret(ctx),
        300,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    app = _build_app(ctx, _FULL)
    async with (
        mcp_lifespan(app, ctx),
        AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client,
    ):
        resp = await client.post("/mcp", json=body, headers=headers)

    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "database_unavailable"
    assert "www-authenticate" not in resp.headers


async def test_open_event_stream_ends_when_user_disabled(
    integration_context: Context, actors: _Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An open ``/events/stream`` re-verifies its credential, so disabling the user
    ends the stream instead of letting it run on indefinitely."""
    ctx = integration_context
    monkeypatch.setattr(events_router, "_STREAM_RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(
        EventStreamService,
        "stream",
        partialmethod(EventStreamService.stream, poll_interval_seconds=0.05),
    )
    token = await _login(ctx)

    async with _client(ctx, _FULL) as client:
        # The ASGI transport returns once the response body ends, so the request
        # only completes if the stream closes itself.
        request = asyncio.create_task(_get(client, "/events/stream", token))
        await asyncio.sleep(0.3)
        assert not request.done(), "the stream must stay open while the user is active"
        with structlog.testing.capture_logs() as logs:
            await UserService(ctx).disable(actors.user_id, identity=_operator(actors))
            resp = await asyncio.wait_for(request, timeout=10)

    assert resp.status_code == 200
    assert "event: heartbeat" in resp.text
    closed = [e for e in logs if e["event"] == "sse_stream_credential_unverified"]
    assert [e["reason"] for e in closed] == ["refused"]
