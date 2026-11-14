"""Integration tests for OAuth token sweeps on credential rotation and user lifecycle changes.

Covers:
- Self-service password change (``AuthService.change_own_password``) sweeps user OAuth
  access/refresh tokens and unredeemed authorization codes while leaving other users'
  tokens and owned-agent grant-channel tokens intact.
- User disable followed by enable does not revive pre-disable user OAuth tokens.
- User delete stamps ``revoked_at`` on the user's OAuth access/refresh token rows.
- Agent JWKS update (``AgentService.update_jwks``) revokes key-channel
  (``oauth_grant_id IS NULL``) token families and evicts warm in-process broker token
  cache entries while leaving grant-channel tokens intact.
- Refresh token rotation racing ``AgentService.update_jwks`` is serialized via the
  agent row lock so no live key-channel refresh token survives once both settle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from jwt.algorithms import OKPAlgorithm
from sqlalchemy import select, text, update

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.repos import AccessTokenRepository, RefreshTokenRepository
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.schemas.auth import ChangePasswordPayload
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import TokenService, _hash_token
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_WRITE,
    APIS_READ,
    BROKER_EXECUTE_PERMISSION,
    ORG_ADMIN,
    USERS_WRITE,
)
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.integration.auth.conftest import (
    LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    LOCAL_LOGIN_PLATFORM_REDIRECT,
)
from tests.integration.auth.seeds import (
    CODE_VERIFIER,
    SEED_PASSWORD,
    code_challenge,
    mint_grant_channel_tokens,
    seed_agent,
    seed_client,
    seed_password_user,
    seed_permissions,
)

pytestmark = pytest.mark.integration

_NEW_PASSWORD = "rotated-self-password-999"  # pragma: allowlist secret


def _make_broker_probe_app(ctx: Context) -> FastAPI:
    """Broker auth + execute-permission dependency wired to ``ctx``."""
    app = FastAPI()
    app.state.ctx = ctx
    install_broker_auth(app, ctx)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]

    @app.post("/probe-execute")
    async def _probe(identity: RequireExecuteAccess) -> dict[str, object]:
        return {"sub": identity.sub, "permissions": identity.permissions}

    return app


def _http_client(app: FastAPI, token: str | None = None) -> AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        headers=headers,
    )


def _ed25519_jwks(kid: str = "k1") -> dict[str, Any]:
    key = Ed25519PrivateKey.generate()
    jwk = OKPAlgorithm().to_jwk(key.public_key())
    jwk_dict = json.loads(jwk) if isinstance(jwk, str) else dict(jwk)
    jwk_dict["kid"] = kid
    return {"keys": [jwk_dict]}


@pytest.mark.asyncio
async def test_change_own_password_revokes_user_oauth_family(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Self-service password change sweeps the user's OAuth tokens and unredeemed codes,
    returns a valid login JWT, and leaves other users' tokens and owned-agent grant-channel
    tokens untouched."""
    ctx = local_login_ctx
    user_id, email = await seed_password_user(ctx, "usr_sweep_pwchange")
    await seed_permissions(ctx, user_id, [APIS_READ, BROKER_EXECUTE_PERMISSION, AGENTS_WRITE])

    other_id, _ = await seed_password_user(ctx, "usr_sweep_pwchange_other")
    await seed_permissions(ctx, other_id, [APIS_READ])

    agent_client_id = await seed_client(ctx, allowed_scopes=[APIS_READ, BROKER_EXECUTE_PERMISSION])
    owned_agent_id = await seed_agent(
        ctx,
        owner_id=user_id,
        scopes=[APIS_READ, BROKER_EXECUTE_PERMISSION],
        name="pwchange-owned-agent",
    )
    _grant_id, grant_at, grant_rt, _ = await mint_grant_channel_tokens(
        ctx,
        user_id=user_id,
        agent_id=owned_agent_id,
        grant_scopes=[APIS_READ, BROKER_EXECUTE_PERMISSION],
        client_id=agent_client_id,
    )

    token_svc = TokenService(ctx)
    authorize_svc = AuthorizeService(ctx)
    auth_svc = AuthService(ctx)

    user_at, user_rt = await token_svc.issue_pair(
        user_id, ActorType.USER, ["openid", APIS_READ, BROKER_EXECUTE_PERMISSION]
    )
    other_at, other_rt = await token_svc.issue_pair(other_id, ActorType.USER, ["openid", APIS_READ])
    unredeemed_code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=f"openid {APIS_READ}",
    )

    caller_identity = Identity(
        sub=user_id,
        email=email,
        actor_type=ActorType.USER,
        permissions=[APIS_READ, BROKER_EXECUTE_PERMISSION, AGENTS_WRITE],
    )
    bundle = await auth_svc.change_own_password(
        ChangePasswordPayload(
            current_password=SEED_PASSWORD,
            new_password=_NEW_PASSWORD,
        ),
        identity=caller_identity,
    )

    # Returned login JWT verifies cleanly.
    verified_jwt = await auth_svc.verify_token(bundle.access_token)
    assert verified_jwt.sub == user_id
    assert verified_jwt.must_change_password is False

    # User's pre-change access token is inactive; refresh token and unredeemed code are rejected.
    resolved_user_at = await token_svc.resolve_access_token(user_at)
    assert resolved_user_at is None or resolved_user_at.active is False

    with pytest.raises(InvalidGrantError, match="refresh token not found or revoked"):
        await token_svc.refresh(user_rt)

    with pytest.raises(InvalidGrantError):
        await authorize_svc.exchange_code(
            code=unredeemed_code,
            code_verifier=CODE_VERIFIER,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        )

    # Another user's token family is unaffected.
    resolved_other_at = await token_svc.resolve_access_token(other_at)
    assert resolved_other_at is not None and resolved_other_at.active is True
    rotated_other_at, _, _ = await token_svc.refresh(other_rt)
    assert rotated_other_at.startswith("at_")

    # Grant-channel AGENT tokens of an agent this user owns are unaffected.
    resolved_grant_at = await token_svc.resolve_access_token(grant_at)
    assert resolved_grant_at is not None and resolved_grant_at.active is True
    rotated_grant_at, _, _ = await token_svc.refresh(grant_rt, client_id=agent_client_id)
    assert rotated_grant_at.startswith("at_")


@pytest.mark.asyncio
async def test_disable_then_enable_does_not_revive_user_oauth_tokens(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Disabling a user sweeps their OAuth tokens and codes so re-enabling the user
    does not revive pre-disable access or refresh tokens."""
    ctx = local_login_ctx
    admin_id, _ = await seed_password_user(ctx, "usr_sweep_disable_admin")
    await seed_permissions(ctx, admin_id, [ORG_ADMIN, USERS_WRITE])
    admin_identity = Identity(
        sub=admin_id,
        actor_type=ActorType.USER,
        permissions=[ORG_ADMIN, USERS_WRITE],
    )

    user_id, _ = await seed_password_user(ctx, "usr_sweep_disable_target")
    await seed_permissions(ctx, user_id, [APIS_READ, BROKER_EXECUTE_PERMISSION])

    token_svc = TokenService(ctx)
    authorize_svc = AuthorizeService(ctx)
    user_svc = UserService(ctx)

    user_at, user_rt = await token_svc.issue_pair(
        user_id, ActorType.USER, ["openid", APIS_READ, BROKER_EXECUTE_PERMISSION]
    )
    unredeemed_code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=f"openid {APIS_READ}",
    )

    await user_svc.disable(user_id, identity=admin_identity)
    await user_svc.enable(user_id, identity=admin_identity)

    resolved_at = await token_svc.resolve_access_token(user_at)
    assert resolved_at is None or resolved_at.active is False
    assert (await token_svc.introspect(user_at))["active"] is False
    assert (await token_svc.introspect(user_rt))["active"] is False

    with pytest.raises(InvalidGrantError, match="refresh token not found or revoked"):
        await token_svc.refresh(user_rt)

    with pytest.raises(InvalidGrantError):
        await authorize_svc.exchange_code(
            code=unredeemed_code,
            code_verifier=CODE_VERIFIER,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        )


@pytest.mark.asyncio
async def test_user_delete_sweeps_oauth_rows(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Deleting a user stamps ``revoked_at`` on the user's OAuth access and refresh token rows."""
    ctx = local_login_ctx
    admin_id, _ = await seed_password_user(ctx, "usr_sweep_delete_admin")
    await seed_permissions(ctx, admin_id, [ORG_ADMIN, USERS_WRITE])
    admin_identity = Identity(
        sub=admin_id,
        actor_type=ActorType.USER,
        permissions=[ORG_ADMIN, USERS_WRITE],
    )

    user_id, _ = await seed_password_user(ctx, "usr_sweep_delete_target")
    await seed_permissions(ctx, user_id, [APIS_READ])

    token_svc = TokenService(ctx)
    user_at, user_rt = await token_svc.issue_pair(user_id, ActorType.USER, ["openid", APIS_READ])

    await UserService(ctx).delete(user_id, identity=admin_identity)

    async with ctx.admin_db.session() as session:
        at_row = await AccessTokenRepository.get_by_hash(session, _hash_token(user_at))
        rt_row = await RefreshTokenRepository.get_by_hash(session, _hash_token(user_rt))
        assert at_row is not None and at_row.revoked_at is not None
        assert rt_row is not None and rt_row.revoked_at is not None


@pytest.mark.asyncio
async def test_update_jwks_revokes_key_channel_family_only(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Replacing an agent's JWKS revokes its key-channel token families and evicts the warm
    in-process broker cache immediately, while leaving grant-channel tokens intact."""
    ctx = local_login_ctx
    prior_ttl = ctx.config.broker.resolve_cache_ttl_seconds
    ctx.config.broker.resolve_cache_ttl_seconds = 3600.0
    try:
        owner_id, _ = await seed_password_user(ctx, "usr_sweep_jwks_owner")
        await seed_permissions(ctx, owner_id, [AGENTS_WRITE, APIS_READ, BROKER_EXECUTE_PERMISSION])
        owner_identity = Identity(
            sub=owner_id,
            actor_type=ActorType.USER,
            permissions=[AGENTS_WRITE, APIS_READ, BROKER_EXECUTE_PERMISSION],
        )

        agent_id = await seed_agent(
            ctx,
            owner_id=owner_id,
            scopes=[APIS_READ, BROKER_EXECUTE_PERMISSION],
            name="jwks-rotation-agent",
        )
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                update(Agent).where(Agent.id == agent_id).values(jwks=_ed25519_jwks("k-old"))
            )

        agent_client_id = await seed_client(
            ctx, allowed_scopes=[APIS_READ, BROKER_EXECUTE_PERMISSION]
        )
        _grant_id, grant_at, grant_rt, _ = await mint_grant_channel_tokens(
            ctx,
            user_id=owner_id,
            agent_id=agent_id,
            grant_scopes=[APIS_READ, BROKER_EXECUTE_PERMISSION],
            client_id=agent_client_id,
        )

        token_svc = TokenService(ctx)
        key_at, key_rt = await token_svc.issue_pair(
            agent_id, ActorType.AGENT, [APIS_READ, BROKER_EXECUTE_PERMISSION]
        )

        broker_app = _make_broker_probe_app(ctx)
        # Warm the in-process broker CachedTokenValidator for both key-channel and grant-channel.
        async with _http_client(broker_app, key_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200
        async with _http_client(broker_app, grant_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200

        await AgentService(ctx).update_jwks(
            agent_id,
            jwks=_ed25519_jwks("k-new"),
            identity=owner_identity,
        )

        # Key-channel pair is revoked and immediately rejected by the warm broker validator.
        resolved_key_at = await token_svc.resolve_access_token(key_at)
        assert resolved_key_at is None or resolved_key_at.active is False
        with pytest.raises(InvalidGrantError, match="refresh token not found or revoked"):
            await token_svc.refresh(key_rt)

        async with _http_client(broker_app, key_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 401

        # Grant-channel pair remains active and rotates cleanly.
        resolved_grant_at = await token_svc.resolve_access_token(grant_at)
        assert resolved_grant_at is not None and resolved_grant_at.active is True
        async with _http_client(broker_app, grant_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200
        rotated_grant_at, _, _ = await token_svc.refresh(grant_rt, client_id=agent_client_id)
        assert rotated_grant_at.startswith("at_")
    finally:
        ctx.config.broker.resolve_cache_ttl_seconds = prior_ttl


async def _wait_for_oauth_insert_lock(ctx: Context, table: str) -> None:
    """Wait until the credential INSERT blocks on its uncommitted unique-hash twin."""
    probe = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE wait_event_type = 'Lock' AND datname = current_database() "
        "AND pid <> pg_backend_pid() AND query ILIKE :statement_pattern"
    )
    async with asyncio.timeout(10):
        while True:
            async with ctx.admin_db.session() as session:
                count = await session.execute(
                    probe, {"statement_pattern": f"%INSERT INTO {table}%"}
                )
                if int(count.scalar_one()) > 0:
                    return
            await asyncio.sleep(0.02)


async def _wait_for_task_or_lock(
    ctx: Context, task: asyncio.Task[Any], *, minimum_lock_waits: int
) -> None:
    """Wait until task finishes or the expected concurrent lock waits are visible."""
    probe = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE wait_event_type = 'Lock' AND datname = current_database() "
        "AND pid <> pg_backend_pid()"
    )
    async with asyncio.timeout(10):
        while not task.done():
            async with ctx.admin_db.session() as session:
                if int((await session.execute(probe)).scalar_one()) >= minimum_lock_waits:
                    return
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_refresh_racing_update_jwks_is_swept(
    local_login_ctx: Context,
    second_auth_process: Context,
    integration_config: AppConfig,
    clean_grants: None,
    clean_user_secrets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key-channel refresh rotation racing ``update_jwks`` either commits first and is
    swept or waits on the agent row lock: after both settle, no live key-channel ``rt_``
    exists for the agent."""
    if integration_config.databases.admin.backend == "sqlite":
        pytest.skip("SQLite does not provide the PostgreSQL row locks used by this race")

    ctx = local_login_ctx
    owner_id, _ = await seed_password_user(ctx, "usr_sweep_jwks_race")
    await seed_permissions(ctx, owner_id, [AGENTS_WRITE, APIS_READ])
    owner_identity = Identity(
        sub=owner_id,
        actor_type=ActorType.USER,
        permissions=[AGENTS_WRITE, APIS_READ],
    )

    agent_id = await seed_agent(
        ctx,
        owner_id=owner_id,
        scopes=[APIS_READ],
        name="jwks-race-agent",
    )
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(Agent).where(Agent.id == agent_id).values(jwks=_ed25519_jwks("k-initial"))
        )

    token_svc = TokenService(ctx)
    _, original_rt = await token_svc.issue_pair(agent_id, ActorType.AGENT, [APIS_READ])

    monkeypatch.setattr(
        "jentic_one.auth.services.token_service._generate_token",
        lambda prefix: f"{prefix}jwks_race_twin",
    )
    blocked_hash = _hash_token("at_jwks_race_twin")

    async def _rotate_jwks() -> None:
        await AgentService(second_auth_process).update_jwks(
            agent_id,
            jwks=_ed25519_jwks("k-rotated"),
            identity=owner_identity,
        )

    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    async with second_auth_process.admin_db.session() as blocker_session:
        await AccessTokenRepository.create(
            blocker_session,
            token_hash=blocked_hash,
            actor_id=agent_id,
            actor_type=ActorType.AGENT,
            scopes=[APIS_READ],
            token_family_id="tfam_jwks_race_blocker",
            expires_at=expires_at,
            created_by=agent_id,
        )

        async def _run_refresh() -> None:
            with contextlib.suppress(InvalidGrantError):
                await token_svc.refresh(original_rt)

        refresh_task = asyncio.create_task(_run_refresh())
        await _wait_for_oauth_insert_lock(ctx, "access_tokens")
        jwks_task = asyncio.create_task(_rotate_jwks())
        try:
            await _wait_for_task_or_lock(ctx, jwks_task, minimum_lock_waits=2)
        finally:
            await blocker_session.rollback()

        await asyncio.gather(refresh_task, jwks_task)

    async with ctx.admin_db.session() as session:
        live_rows = (
            (
                await session.execute(
                    select(RefreshToken).where(
                        RefreshToken.actor_id == agent_id,
                        RefreshToken.actor_type == ActorType.AGENT.value,
                        RefreshToken.oauth_grant_id.is_(None),
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live_rows == []
