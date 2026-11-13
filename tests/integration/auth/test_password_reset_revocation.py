"""Integration tests for operator password-reset OAuth containment (#1297).

Pins that across ``auth`` and ``broker``:
- Operator-initiated password rotation (``AuthService.reset_password``) revokes
  in-flight USER OAuth access tokens, refresh tokens, and unredeemed authorization
  codes, and evicts warm in-process ``CachedTokenValidator`` entries immediately.
- ``must_change_password=True`` fences USER OAuth code issuance, code exchange,
  new consent-grant creation, token resolution, introspection, refresh rotation,
  and queued-execution user authority until the required password change completes.
- Already-established agent-bound ``oauth_client_grants`` rows and ``AGENT`` tokens,
  as well as other users' tokens, codes, and grants, remain unaffected.
- Completing ``POST /users/me:change-password`` clears the rotation gate for new
  logins and OAuth flows (preserving the #1442 live user permission ceiling and
  permission narrowing) without reviving pre-reset tokens or codes.
"""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.admin.repos import OAuthClientRepository, UserRepository
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import ConsentAgentNotEligibleError, InvalidGrantError
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    APIS_READ,
    BROKER_EXECUTE_PERMISSION,
    ORG_ADMIN,
    USERS_READ,
    USERS_WRITE,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.app_factory import create_combined_app
from tests.integration.auth.conftest import (
    LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    LOCAL_LOGIN_PLATFORM_REDIRECT,
)
from tests.integration.auth.seeds import (
    CODE_VERIFIER,
    SEED_MARKER,
    SEED_PASSWORD,
    code_challenge,
    mint_grant_channel_tokens,
    seed_agent,
    seed_client,
    seed_password_user,
    seed_permissions,
)

pytestmark = pytest.mark.integration

_TEMP_PASSWORD = "temporary-pass-1297"  # pragma: allowlist secret
_ROTATED_PASSWORD = "rotated-password-1297"  # pragma: allowlist secret
_USER_CLIENT_ID = "oc_1297_user_client"
_USER_CLIENT_REDIRECT = "https://user-client.1297.test/cb"


def _make_control_app(ctx: Context) -> FastAPI:
    return create_combined_app(ctx, ["registry", "admin", "control", "auth"])


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


def _form_hidden_fields(html: str, *names: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in names:
        marker = f'name="{name}" value="'
        start = html.index(marker) + len(marker)
        fields[name] = html[start : html.index('"', start)]
    return fields


async def _seed_user_consent_client(ctx: Context, client_id: str = _USER_CLIENT_ID) -> None:
    async with ctx.admin_db.session() as session:
        await OAuthClientRepository.create(
            session,
            client_id=client_id,
            name="User Consent App 1297",
            redirect_uris=[_USER_CLIENT_REDIRECT],
            client_secret_hash=None,
            allowed_scopes=["openid", "offline_access", APIS_READ, BROKER_EXECUTE_PERMISSION],
            token_endpoint_auth_method="none",
            consent_model="user",
            created_by=SEED_MARKER,
        )
        await session.commit()


async def _mint_platform_code(
    app: FastAPI,
    *,
    email: str,
    scope: str,
    password: str = SEED_PASSWORD,
) -> str:
    """Run GET /authorize -> GET /login -> POST /login and return the platform auth code."""
    async with _http_client(app) as client:
        auth_resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": LOCAL_LOGIN_PLATFORM_CLIENT_ID,
                "redirect_uri": LOCAL_LOGIN_PLATFORM_REDIRECT,
                "code_challenge": code_challenge(CODE_VERIFIER),
                "code_challenge_method": "S256",
                "scope": scope,
                "state": f"pkce-{secrets.token_urlsafe(8)}",
                "nonce": f"nonce-{secrets.token_urlsafe(8)}",
            },
        )
        assert auth_resp.status_code == 302, auth_resp.text
        login_url = auth_resp.headers["location"]
        assert login_url.startswith("/login?ls="), login_url

        form_resp = await client.get(login_url)
        assert form_resp.status_code == 200, form_resp.text
        fields = _form_hidden_fields(form_resp.text, "ls", "csrf")

        submit_resp = await client.post(
            "/login",
            data={"email": email, "password": password, **fields},
        )
        assert submit_resp.status_code == 302, submit_resp.text
        cb_location = submit_resp.headers["location"]
        assert cb_location.startswith(f"{LOCAL_LOGIN_PLATFORM_REDIRECT}?"), cb_location
        return parse_qs(urlsplit(cb_location).query)["code"][0]


async def _mint_platform_pkce_tokens(
    app: FastAPI,
    *,
    email: str,
    scope: str,
    password: str = SEED_PASSWORD,
) -> dict[str, object]:
    """Run the full platform PKCE flow and return the token response body."""
    code = await _mint_platform_code(app, email=email, scope=scope, password=password)
    async with _http_client(app) as client:
        tok_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": LOCAL_LOGIN_PLATFORM_REDIRECT,
                "client_id": LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            },
        )
        assert tok_resp.status_code == 200, tok_resp.text
        body: dict[str, object] = tok_resp.json()
        return body


async def _start_third_party_consent(
    app: FastAPI,
    *,
    email: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    password: str = SEED_PASSWORD,
) -> str:
    """Authenticate through /authorize -> /login for a third-party client; return ``ch``."""
    async with _http_client(app) as client:
        auth_resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge(CODE_VERIFIER),
                "code_challenge_method": "S256",
                "scope": scope,
                "state": f"consent-{secrets.token_urlsafe(8)}",
                "nonce": f"nonce-{secrets.token_urlsafe(8)}",
            },
        )
        assert auth_resp.status_code == 302, auth_resp.text
        login_url = auth_resp.headers["location"]
        form_resp = await client.get(login_url)
        assert form_resp.status_code == 200, form_resp.text
        fields = _form_hidden_fields(form_resp.text, "ls", "csrf")

        submit_resp = await client.post(
            "/login",
            data={"email": email, "password": password, **fields},
        )
        assert submit_resp.status_code == 302, submit_resp.text
        consent_loc = submit_resp.headers["location"]
        assert consent_loc.startswith("/oauth/consent?ch="), consent_loc
        return parse_qs(urlsplit(consent_loc).query)["ch"][0]


async def _mint_user_client_pkce_tokens(
    app: FastAPI,
    *,
    email: str,
    scope: str,
    password: str = SEED_PASSWORD,
) -> dict[str, object]:
    """Run /authorize -> /login -> /oauth/consent -> /oauth/token for consent_model='user'."""
    ch = await _start_third_party_consent(
        app,
        email=email,
        client_id=_USER_CLIENT_ID,
        redirect_uri=_USER_CLIENT_REDIRECT,
        scope=scope,
        password=password,
    )
    async with _http_client(app) as client:
        consent_resp = await client.post(
            "/oauth/consent",
            data={"consent_token": ch, "action": "approve"},
        )
        assert consent_resp.status_code == 302, consent_resp.text
        cb_loc = consent_resp.headers["location"]
        assert cb_loc.startswith(f"{_USER_CLIENT_REDIRECT}?"), cb_loc
        code = parse_qs(urlsplit(cb_loc).query)["code"][0]

        tok_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _USER_CLIENT_REDIRECT,
                "client_id": _USER_CLIENT_ID,
            },
        )
        assert tok_resp.status_code == 200, tok_resp.text
        body: dict[str, object] = tok_resp.json()
        return body


@pytest.mark.asyncio
async def test_operator_password_reset_revokes_user_oauth_tokens_and_warm_broker_cache(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1297: ``AuthService.reset_password`` revokes all USER OAuth access/refresh
    tokens across ``auth`` (verifier + introspection + refresh) and ``broker`` (including a
    warm ``CachedTokenValidator``), while leaving another user's tokens untouched."""
    ctx = local_login_ctx
    prior_ttl = ctx.config.broker.resolve_cache_ttl_seconds
    ctx.config.broker.resolve_cache_ttl_seconds = 3600.0
    try:
        target_id, target_email = await seed_password_user(ctx, "usr_1297_target")
        await seed_permissions(ctx, target_id, [BROKER_EXECUTE_PERMISSION, USERS_WRITE])

        other_id, other_email = await seed_password_user(ctx, "usr_1297_other")
        await seed_permissions(ctx, other_id, [BROKER_EXECUTE_PERMISSION, USERS_READ])

        await _seed_user_consent_client(ctx)

        control_app = _make_control_app(ctx)
        broker_app = _make_broker_probe_app(ctx)
        token_svc = TokenService(ctx)
        broker_resolver = InProcessTokenResolver(ctx.admin_db)

        platform_pair = await _mint_platform_pkce_tokens(
            control_app,
            email=target_email,
            scope=f"openid offline_access {BROKER_EXECUTE_PERMISSION} {APIS_READ} {USERS_READ}",
        )
        at_platform = str(platform_pair["access_token"])
        rt_platform = str(platform_pair["refresh_token"])

        spa_pair = await _mint_platform_pkce_tokens(
            control_app,
            email=target_email,
            scope="openid email profile",
        )
        at_spa = str(spa_pair["access_token"])
        rt_spa = str(spa_pair["refresh_token"])

        user_client_pair = await _mint_user_client_pkce_tokens(
            control_app,
            email=target_email,
            scope=f"openid offline_access {BROKER_EXECUTE_PERMISSION} {APIS_READ}",
        )
        at_user_client = str(user_client_pair["access_token"])
        rt_user_client = str(user_client_pair["refresh_token"])

        at_ephemeral = await token_svc.issue_access_only(
            target_id, ActorType.USER, [BROKER_EXECUTE_PERMISSION, USERS_READ]
        )

        async with _http_client(control_app) as client:
            login_resp = await client.post(
                "/auth/login",
                json={"email": target_email, "password": SEED_PASSWORD},
            )
            assert login_resp.status_code == 200, login_resp.text
            pre_reset_jwt = str(login_resp.json()["access_token"])

        other_pair = await _mint_platform_pkce_tokens(
            control_app,
            email=other_email,
            scope=f"openid offline_access {BROKER_EXECUTE_PERMISSION} {USERS_READ}",
        )
        other_at = str(other_pair["access_token"])
        other_rt = str(other_pair["refresh_token"])

        # Warm control-plane verifier and broker CachedTokenValidator before reset.
        for tok in (at_platform, at_spa, at_ephemeral, other_at):
            async with _http_client(control_app, tok) as ctrl_client:
                assert (await ctrl_client.get("/users")).status_code == 200
                assert (await ctrl_client.get("/me")).status_code == 200
        async with _http_client(control_app, at_user_client) as ctrl_client:
            assert (await ctrl_client.get("/me")).status_code == 200

        for tok in (at_platform, at_user_client, at_ephemeral, other_at):
            async with _http_client(broker_app, tok) as broker_client:
                assert (await broker_client.post("/probe-execute")).status_code == 200

        # Operator forces password rotation on target_user.
        returned_id = await AuthService(ctx).reset_password(
            email=target_email, temporary_password=_TEMP_PASSWORD
        )
        assert returned_id == target_id

        # Every pre-reset USER access token of target_user is immediately inactive everywhere.
        for tok in (at_platform, at_spa, at_user_client, at_ephemeral):
            auth_resolved = await token_svc.resolve_access_token(tok)
            assert auth_resolved is not None and auth_resolved.active is False
            broker_resolved = await broker_resolver.resolve_access_token(tok)
            assert broker_resolved is not None and broker_resolved.active is False
            at_intro = await token_svc.introspect(tok)
            assert at_intro["active"] is False
            assert at_intro["scope"] == ""

            async with _http_client(control_app, other_at) as introspect_client:
                isp_resp = await introspect_client.post("/oauth/introspect", json={"token": tok})
                assert isp_resp.status_code == 200
                assert isp_resp.json()["active"] is False

            async with _http_client(control_app, tok) as ctrl_client:
                assert (await ctrl_client.get("/users")).status_code == 401
                assert (await ctrl_client.get("/me")).status_code == 401

            async with _http_client(broker_app, tok) as broker_client:
                assert (await broker_client.post("/probe-execute")).status_code == 401

        # Pre-reset login JWT remains fenced via session_generation.
        async with _http_client(control_app, pre_reset_jwt) as ctrl_client:
            assert (await ctrl_client.get("/users/me")).status_code == 401

        # Every pre-reset USER refresh token of target_user is dead and cannot rotate.
        for rt, client_id in (
            (rt_platform, None),
            (rt_spa, None),
            (rt_user_client, _USER_CLIENT_ID),
        ):
            rt_intro = await token_svc.introspect(rt)
            assert rt_intro["active"] is False
            assert rt_intro["scope"] == ""
            payload: dict[str, str] = {"grant_type": "refresh_token", "refresh_token": rt}
            if client_id is not None:
                payload["client_id"] = client_id
            async with _http_client(control_app) as client:
                ref_resp = await client.post("/oauth/token", json=payload)
            assert ref_resp.status_code == 400, ref_resp.text
            assert ref_resp.json()["error"] == "invalid_grant"

        # Other user's tokens remain completely isolated and functional.
        async with _http_client(control_app, other_at) as ctrl_client:
            assert (await ctrl_client.get("/users")).status_code == 200
        async with _http_client(broker_app, other_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200
        assert (await token_svc.introspect(other_at))["active"] is True

        async with _http_client(control_app) as client:
            other_ref = await client.post(
                "/oauth/token",
                json={"grant_type": "refresh_token", "refresh_token": other_rt},
            )
        assert other_ref.status_code == 200, other_ref.text
    finally:
        ctx.config.broker.resolve_cache_ttl_seconds = prior_ttl


@pytest.mark.asyncio
async def test_operator_password_reset_invalidates_unredeemed_codes_and_preserves_agent_grants(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1297: operator password reset consumes in-flight authorization codes and
    refuses pre-reset consent handles, without collateralizing established agent-bound
    ``oauth_client_grants`` or another user's codes and grants."""
    ctx = local_login_ctx
    target_id, target_email = await seed_password_user(ctx, "usr_1297_codes_target")
    await seed_permissions(ctx, target_id, [APIS_READ, BROKER_EXECUTE_PERMISSION])

    other_id, other_email = await seed_password_user(ctx, "usr_1297_codes_other")
    await seed_permissions(ctx, other_id, [APIS_READ])

    await _seed_user_consent_client(ctx)
    agent_client_id = await seed_client(ctx, allowed_scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ])

    target_agent_id = await seed_agent(
        ctx,
        owner_id=target_id,
        scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ],
        name="target-owned-agent",
    )
    other_agent_id = await seed_agent(
        ctx,
        owner_id=other_id,
        scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ],
        name="other-owned-agent",
    )

    grant_id, agent_at, agent_rt, _ = await mint_grant_channel_tokens(
        ctx,
        user_id=target_id,
        agent_id=target_agent_id,
        grant_scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ],
        client_id=agent_client_id,
    )
    other_grant_id, other_agent_at, other_agent_rt, _ = await mint_grant_channel_tokens(
        ctx,
        user_id=other_id,
        agent_id=other_agent_id,
        grant_scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ],
        client_id=agent_client_id,
    )

    control_app = _make_control_app(ctx)
    broker_app = _make_broker_probe_app(ctx)
    authorize_svc = AuthorizeService(ctx)

    # Mint unredeemed codes and an in-flight consent handle BEFORE password reset.
    unredeemed_user_code = await _mint_platform_code(
        control_app, email=target_email, scope=f"openid {APIS_READ}"
    )
    unredeemed_grant_code = await authorize_svc.issue_authorization_code(
        user_id=target_id,
        client_id=agent_client_id,
        redirect_uri="https://mcpapp.example.com/cb",
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=APIS_READ,
        grant_id=grant_id,
    )
    in_flight_user_consent_ch = await _start_third_party_consent(
        control_app,
        email=target_email,
        client_id=_USER_CLIENT_ID,
        redirect_uri=_USER_CLIENT_REDIRECT,
        scope=f"openid {APIS_READ}",
    )
    in_flight_agent_consent_ch = await _start_third_party_consent(
        control_app,
        email=target_email,
        client_id=agent_client_id,
        redirect_uri="https://mcpapp.example.com/cb",
        scope=APIS_READ,
    )
    other_unredeemed_code = await _mint_platform_code(
        control_app, email=other_email, scope=f"openid {APIS_READ}"
    )

    await AuthService(ctx).reset_password(email=target_email, temporary_password=_TEMP_PASSWORD)

    # Target user's pre-reset unredeemed codes are rejected at /oauth/token.
    async with _http_client(control_app) as client:
        resp_user_code = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": unredeemed_user_code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": LOCAL_LOGIN_PLATFORM_REDIRECT,
                "client_id": LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            },
        )
        assert resp_user_code.status_code == 400, resp_user_code.text
        assert resp_user_code.json()["error"] == "invalid_grant"

        resp_grant_code = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": unredeemed_grant_code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": "https://mcpapp.example.com/cb",
                "client_id": agent_client_id,
            },
        )
        assert resp_grant_code.status_code == 400, resp_grant_code.text
        assert resp_grant_code.json()["error"] == "invalid_grant"

        # Pre-reset consent handles cannot mint fresh codes or grants after reset.
        user_consent_post = await client.post(
            "/oauth/consent",
            data={"consent_token": in_flight_user_consent_ch, "action": "approve"},
        )
        assert user_consent_post.status_code == 302
        assert user_consent_post.headers["location"].startswith("/error?error=")

        agent_consent_post = await client.post(
            "/oauth/consent",
            data={
                "consent_token": in_flight_agent_consent_ch,
                "action": "approve",
                "agent_id": target_agent_id,
            },
        )
        assert agent_consent_post.status_code == 302
        assert agent_consent_post.headers["location"].startswith("/error?error=")

        # Other user's unredeemed code still exchanges cleanly.
        other_exch = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": other_unredeemed_code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": LOCAL_LOGIN_PLATFORM_REDIRECT,
                "client_id": LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            },
        )
        assert other_exch.status_code == 200, other_exch.text

    # Already-established agent-bound oauth_client_grants and AGENT tokens stay active.
    grant_svc = OAuthGrantService(ctx)
    token_svc = TokenService(ctx)
    target_grant = await grant_svc.get_grant(grant_id)
    other_grant = await grant_svc.get_grant(other_grant_id)
    assert target_grant is not None and target_grant.status == "active"
    assert other_grant is not None and other_grant.status == "active"

    for ag_at, ag_rt in ((agent_at, agent_rt), (other_agent_at, other_agent_rt)):
        resolved = await token_svc.resolve_access_token(ag_at)
        assert resolved is not None and resolved.active is True
        async with _http_client(broker_app, ag_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200
        rotated_at, _rotated_rt, _ = await token_svc.refresh(ag_rt, client_id=agent_client_id)
        assert rotated_at.startswith("at_")


@pytest.mark.asyncio
async def test_must_change_password_flag_fences_user_oauth_issuance_resolution_and_refresh(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1297: ``must_change_password=True`` on the ``users`` row fences USER
    token resolution, introspection, refresh, queued-execution authority, code
    issuance/exchange, and new consent-grant creation even without a sweep."""
    ctx = local_login_ctx
    user_id, _ = await seed_password_user(ctx, "usr_1297_flag_fence")
    await seed_permissions(ctx, user_id, [BROKER_EXECUTE_PERMISSION, APIS_READ])
    agent_id = await seed_agent(ctx, owner_id=user_id, scopes=[APIS_READ])
    client_id = await seed_client(ctx, allowed_scopes=[APIS_READ])

    token_svc = TokenService(ctx)
    authorize_svc = AuthorizeService(ctx)
    broker_resolver = InProcessTokenResolver(ctx.admin_db)
    status_resolver = ActorStatusResolver(ctx.admin_db)

    access, refresh = await token_svc.issue_pair(
        user_id, ActorType.USER, ["openid", BROKER_EXECUTE_PERMISSION, APIS_READ]
    )
    pre_flag_code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=f"openid {APIS_READ}",
    )

    # Flip must_change_password directly without running reset_password's sweep.
    async with ctx.admin_db.transaction() as session:
        await UserRepository.update(session, user_id, must_change_password=True)

    auth_resolved = await token_svc.resolve_access_token(access)
    assert auth_resolved is not None and auth_resolved.active is False
    broker_resolved = await broker_resolver.resolve_access_token(access)
    assert broker_resolved is not None and broker_resolved.active is False
    assert (await token_svc.introspect(access))["active"] is False
    assert (await token_svc.introspect(refresh))["active"] is False

    authority = await status_resolver.authority(
        actor_id=user_id,
        actor_type=ActorType.USER.value,
        permission=BROKER_EXECUTE_PERMISSION,
    )
    assert authority.active is False

    with pytest.raises(InvalidGrantError):
        await token_svc.refresh(refresh)

    with pytest.raises(InvalidGrantError):
        await authorize_svc.issue_authorization_code(
            user_id=user_id,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
            code_challenge=code_challenge(CODE_VERIFIER),
            scopes=f"openid {APIS_READ}",
        )

    with pytest.raises(InvalidGrantError):
        await authorize_svc.exchange_code(
            code=pre_flag_code,
            code_verifier=CODE_VERIFIER,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        )

    with pytest.raises(ConsentAgentNotEligibleError):
        await OAuthGrantService(ctx).create_grant(
            user_id=user_id,
            oauth_client_id=client_id,
            agent_id=agent_id,
            scopes=[APIS_READ],
        )


@pytest.mark.asyncio
async def test_change_password_restores_oauth_flow_without_reviving_pre_reset_tokens(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1297 + #1442: completing ``POST /users/me:change-password`` after an
    operator reset allows fresh login and OAuth token minting across ``auth`` and
    ``broker`` (with live permission ceiling and narrowing intact) while pre-reset
    OAuth tokens, refresh tokens, and codes remain permanently revoked."""
    ctx = local_login_ctx
    prior_ttl = ctx.config.broker.resolve_cache_ttl_seconds
    ctx.config.broker.resolve_cache_ttl_seconds = 3600.0
    try:
        admin_id, _ = await seed_password_user(ctx, "usr_1297_admin")
        await seed_permissions(ctx, admin_id, [ORG_ADMIN])
        admin_identity = Identity(sub=admin_id, actor_type=ActorType.USER, permissions=[ORG_ADMIN])

        user_id, email = await seed_password_user(ctx, "usr_1297_rotate")
        await seed_permissions(ctx, user_id, [BROKER_EXECUTE_PERMISSION, USERS_WRITE])

        control_app = _make_control_app(ctx)
        broker_app = _make_broker_probe_app(ctx)
        token_svc = TokenService(ctx)

        pre_pair = await _mint_platform_pkce_tokens(
            control_app,
            email=email,
            scope=f"openid offline_access {BROKER_EXECUTE_PERMISSION} {APIS_READ} {USERS_READ}",
        )
        pre_at = str(pre_pair["access_token"])
        pre_rt = str(pre_pair["refresh_token"])
        pre_code = await _mint_platform_code(control_app, email=email, scope=f"openid {APIS_READ}")

        async with _http_client(broker_app, pre_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200

        await AuthService(ctx).reset_password(email=email, temporary_password=_TEMP_PASSWORD)

        # Log in with temporary password -> fenced login JWT -> rotate via change-password.
        async with _http_client(control_app) as client:
            temp_login = await client.post(
                "/auth/login",
                json={"email": email, "password": _TEMP_PASSWORD},
            )
            assert temp_login.status_code == 200, temp_login.text
            temp_body = temp_login.json()
            assert temp_body["must_change_password"] is True
            temp_jwt = str(temp_body["access_token"])

        async with _http_client(control_app, temp_jwt) as gated_client:
            fenced_users = await gated_client.get("/users")
            assert fenced_users.status_code == 403
            assert fenced_users.json()["type"] == "password_rotation_required"

            me_resp = await gated_client.get("/users/me")
            assert me_resp.status_code == 200
            assert me_resp.json()["must_change_password"] is True

            change_resp = await gated_client.post(
                "/users/me:change-password",
                json={
                    "current_password": _TEMP_PASSWORD,
                    "new_password": _ROTATED_PASSWORD,
                },
            )
            assert change_resp.status_code == 200, change_resp.text
            change_body = change_resp.json()
            assert change_body["must_change_password"] is False
            fresh_jwt = str(change_body["access_token"])

        async with _http_client(control_app, fresh_jwt) as fresh_jwt_client:
            assert (await fresh_jwt_client.get("/users")).status_code == 200

        # Clearing must_change_password MUST NOT revive pre-reset OAuth tokens or codes.
        assert (await token_svc.introspect(pre_at))["active"] is False
        assert (await token_svc.introspect(pre_rt))["active"] is False
        async with _http_client(control_app, pre_at) as ctrl_client:
            assert (await ctrl_client.get("/users")).status_code == 401
        async with _http_client(broker_app, pre_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 401
        async with _http_client(control_app) as client:
            assert (
                await client.post(
                    "/oauth/token",
                    json={"grant_type": "refresh_token", "refresh_token": pre_rt},
                )
            ).status_code == 400
            assert (
                await client.post(
                    "/oauth/token",
                    json={
                        "grant_type": "authorization_code",
                        "code": pre_code,
                        "code_verifier": CODE_VERIFIER,
                        "redirect_uri": LOCAL_LOGIN_PLATFORM_REDIRECT,
                        "client_id": LOCAL_LOGIN_PLATFORM_CLIENT_ID,
                    },
                )
            ).status_code == 400

        # New PKCE OAuth minting succeeds with rotated password and enforces #1442 ceiling.
        post_pair = await _mint_platform_pkce_tokens(
            control_app,
            email=email,
            password=_ROTATED_PASSWORD,
            scope=(
                f"openid offline_access {BROKER_EXECUTE_PERMISSION} "
                f"{APIS_READ} {USERS_READ} {ORG_ADMIN}"
            ),
        )
        expected_post_scopes = [
            "openid",
            "offline_access",
            BROKER_EXECUTE_PERMISSION,
            APIS_READ,
            USERS_READ,
        ]
        assert post_pair.get("scope") == " ".join(expected_post_scopes)
        post_at = str(post_pair["access_token"])
        post_rt = str(post_pair["refresh_token"])

        async with _http_client(control_app, post_at) as ctrl_client:
            assert (await ctrl_client.get("/users")).status_code == 200
            assert (await ctrl_client.get("/me")).status_code == 200
        async with _http_client(broker_app, post_at) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200

        # Permission narrowing (#1442) continues to apply live across auth, broker, and refresh.
        await PermissionService(ctx).set_assigned(user_id, [APIS_READ], identity=admin_identity)

        async with _http_client(control_app, post_at) as ctrl_client:
            assert (await ctrl_client.get("/users")).status_code == 403
        async with _http_client(broker_app, post_at) as broker_client:
            denied = await broker_client.post("/probe-execute")
            assert denied.status_code == 403
            assert denied.json()["type"] == "insufficient_scope"

        rotated_at, _rotated_rt, rotated_scopes = await token_svc.refresh(post_rt)
        assert rotated_scopes == ["openid", "offline_access", APIS_READ]
        assert (await token_svc.introspect(rotated_at))["scope"] == (
            f"openid offline_access {APIS_READ}"
        )
    finally:
        ctx.config.broker.resolve_cache_ttl_seconds = prior_ttl
