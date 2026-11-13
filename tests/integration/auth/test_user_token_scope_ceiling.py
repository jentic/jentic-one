"""Integration tests for USER OAuth token scope authorization and live narrowing (#1442).

Pins that across issuance, resolution, introspection, and refresh:
- Platform-client and ``consent_model='user'`` authorization-code + PKCE flows
  authorize requested scopes against the user's live ``user_permission_grants``
  expanded through ``compute_effective(...)``, plus OIDC passthrough scopes
  (``openid``, ``profile``, ``email``, ``offline_access``).
- Ordinary active users cannot obtain unheld privileges (such as
  ``capabilities:execute`` or ``org:admin``) on issued tokens, on the control-plane
  verifier, or on the broker's ``require_execute_permission`` gate.
- Implication-expanded grants (e.g. ``apis:write`` -> ``apis:read``,
  ``catalog:import``) authorize implied scopes without special-casing
  ``org:admin``.
- Narrowing a user's ``user_permission_grants`` immediately strips dropped
  permissions on both ``TokenService.resolve_access_token`` and the broker's
  ``InProcessTokenResolver`` (evicting in-process broker caches), on
  introspection, and on ``refresh_token`` rotation.
- First-party OIDC-only SPA sessions (``scope="openid email profile"``) retain
  the user's live control-plane permissions and reflect narrowing immediately.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.admin.repos import (
    AccessTokenRepository,
    AuthorizationCodeRepository,
    OAuthClientRepository,
    RefreshTokenRepository,
)
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.broker.web.deps import RequireExecuteAccess
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    APIS_READ,
    APIS_WRITE,
    BROKER_EXECUTE_PERMISSION,
    CATALOG_IMPORT,
    CREDENTIALS_CONNECT,
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
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

_USER_CLIENT_ID = "oc_1442_user_client"
_USER_CLIENT_REDIRECT = "https://user-client.1442.test/cb"


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


def _login_form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("ls", "csrf"):
        marker = f'name="{name}" value="'
        start = html.index(marker) + len(marker)
        fields[name] = html[start : html.index('"', start)]
    return fields


async def _mint_platform_pkce_tokens(
    app: FastAPI,
    *,
    email: str,
    scope: str,
    password: str = SEED_PASSWORD,
) -> dict[str, object]:
    """Run GET /authorize -> GET /login -> POST /login -> POST /oauth/token."""
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
                "state": "pkce-1442",
                "nonce": "nonce-1442",
            },
        )
        assert auth_resp.status_code == 302, auth_resp.text
        login_url = auth_resp.headers["location"]
        assert login_url.startswith("/login?ls="), login_url

        form_resp = await client.get(login_url)
        assert form_resp.status_code == 200, form_resp.text
        fields = _login_form_fields(form_resp.text)

        submit_resp = await client.post(
            "/login",
            data={"email": email, "password": password, **fields},
        )
        assert submit_resp.status_code == 302, submit_resp.text
        cb_location = submit_resp.headers["location"]
        assert cb_location.startswith(f"{LOCAL_LOGIN_PLATFORM_REDIRECT}?"), cb_location
        code = parse_qs(urlsplit(cb_location).query)["code"][0]

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


@pytest.mark.asyncio
async def test_ordinary_user_cannot_obtain_unheld_privileges_via_platform_pkce_flow(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1442: an active user holding only ``apis:read`` cannot mint or
    exercise ``capabilities:execute`` or ``org:admin`` through the platform PKCE flow."""
    ctx = local_login_ctx
    user_id, email = await seed_password_user(ctx, "usr_1442_ordinary")
    await seed_permissions(ctx, user_id, [APIS_READ])

    control_app = _make_control_app(ctx)
    broker_app = _make_broker_probe_app(ctx)

    requested_scope = (
        f"openid email profile offline_access {APIS_READ} {BROKER_EXECUTE_PERMISSION} {ORG_ADMIN}"
    )
    token_body = await _mint_platform_pkce_tokens(control_app, email=email, scope=requested_scope)

    expected_scopes = ["openid", "email", "profile", "offline_access", APIS_READ]
    assert token_body.get("id_token") is not None
    assert token_body.get("scope") == " ".join(expected_scopes)

    access_token = str(token_body["access_token"])

    auth_resolved = await TokenService(ctx).resolve_access_token(access_token)
    assert auth_resolved is not None and auth_resolved.active is True
    assert auth_resolved.permissions == expected_scopes

    broker_resolved = await InProcessTokenResolver(ctx.admin_db).resolve_access_token(access_token)
    assert broker_resolved is not None and broker_resolved.active is True
    assert broker_resolved.permissions == expected_scopes

    async with _http_client(broker_app, access_token) as broker_client:
        exec_resp = await broker_client.post("/probe-execute")
    assert exec_resp.status_code == 403, exec_resp.text
    assert exec_resp.json()["type"] == "insufficient_scope"

    async with _http_client(control_app, access_token) as ctrl_client:
        users_resp = await ctrl_client.get("/users")
        me_resp = await ctrl_client.get("/me")
    assert users_resp.status_code == 403, users_resp.text
    assert me_resp.status_code == 200, me_resp.text
    me_body = me_resp.json()
    assert me_body["admin"] is False
    assert ORG_ADMIN not in me_body["permissions"]
    assert BROKER_EXECUTE_PERMISSION not in me_body["permissions"]
    assert APIS_READ in me_body["permissions"]


@pytest.mark.asyncio
async def test_implication_expanded_user_grants_authorize_implied_scopes_without_org_admin(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1442: ``compute_effective`` expands non-admin implying grants
    (``apis:write``, ``credentials:write``) and ``org:admin`` alike at issuance and resolution."""
    ctx = local_login_ctx
    writer_id, writer_email = await seed_password_user(ctx, "usr_1442_writer")
    await seed_permissions(ctx, writer_id, [APIS_WRITE, CREDENTIALS_WRITE])

    admin_id, admin_email = await seed_password_user(ctx, "usr_1442_admin_imp")
    await seed_permissions(ctx, admin_id, [ORG_ADMIN])

    control_app = _make_control_app(ctx)

    writer_body = await _mint_platform_pkce_tokens(
        control_app,
        email=writer_email,
        scope=(
            f"openid {APIS_READ} {CATALOG_IMPORT} {CREDENTIALS_READ} "
            f"{CREDENTIALS_CONNECT} {USERS_READ} {BROKER_EXECUTE_PERMISSION}"
        ),
    )
    expected_writer = [
        "openid",
        APIS_READ,
        CATALOG_IMPORT,
        CREDENTIALS_READ,
        CREDENTIALS_CONNECT,
    ]
    assert writer_body.get("scope") == " ".join(expected_writer)
    writer_at = str(writer_body["access_token"])
    for resolver in (TokenService(ctx), InProcessTokenResolver(ctx.admin_db)):
        resolved = await resolver.resolve_access_token(writer_at)
        assert resolved is not None and resolved.permissions == expected_writer

    admin_body = await _mint_platform_pkce_tokens(
        control_app,
        email=admin_email,
        scope=f"openid {BROKER_EXECUTE_PERMISSION} {USERS_READ}",
    )
    expected_admin = ["openid", BROKER_EXECUTE_PERMISSION, USERS_READ]
    assert admin_body.get("scope") == " ".join(expected_admin)
    admin_at = str(admin_body["access_token"])
    for resolver in (TokenService(ctx), InProcessTokenResolver(ctx.admin_db)):
        resolved = await resolver.resolve_access_token(admin_at)
        assert resolved is not None and resolved.permissions == expected_admin


@pytest.mark.asyncio
async def test_permission_narrowing_drops_revoked_scopes_on_resolve_introspect_broker_and_refresh(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1442: narrowing ``user_permission_grants`` immediately strips the
    revoked permissions on auth and broker resolvers, introspection, and refresh rotation."""
    ctx = local_login_ctx
    prior_ttl = ctx.config.broker.resolve_cache_ttl_seconds
    ctx.config.broker.resolve_cache_ttl_seconds = 3600.0
    try:
        admin_id, _ = await seed_password_user(ctx, "usr_1442_op_admin")
        await seed_permissions(ctx, admin_id, [ORG_ADMIN])
        admin_identity = Identity(sub=admin_id, actor_type=ActorType.USER, permissions=[ORG_ADMIN])

        user_id, email = await seed_password_user(ctx, "usr_1442_narrow")
        await seed_permissions(ctx, user_id, [BROKER_EXECUTE_PERMISSION, USERS_WRITE])

        control_app = _make_control_app(ctx)
        broker_app = _make_broker_probe_app(ctx)

        minted = await _mint_platform_pkce_tokens(
            control_app,
            email=email,
            scope=f"openid offline_access {BROKER_EXECUTE_PERMISSION} {APIS_READ} {USERS_READ}",
        )
        at1 = str(minted["access_token"])
        rt1 = str(minted["refresh_token"])

        initial_expected = [
            "openid",
            "offline_access",
            BROKER_EXECUTE_PERMISSION,
            APIS_READ,
            USERS_READ,
        ]
        assert minted.get("scope") == " ".join(initial_expected)

        # Warm both HTTP surfaces before narrowing.
        async with _http_client(control_app, at1) as ctrl_client:
            assert (await ctrl_client.get("/users")).status_code == 200
        async with _http_client(broker_app, at1) as broker_client:
            assert (await broker_client.post("/probe-execute")).status_code == 200

        # Narrow user grants to apis:read only (drops capabilities:execute and users:write).
        await PermissionService(ctx).set_assigned(user_id, [APIS_READ], identity=admin_identity)

        narrowed_expected = ["openid", "offline_access", APIS_READ]
        token_svc = TokenService(ctx)
        broker_resolver = InProcessTokenResolver(ctx.admin_db)

        for resolver in (token_svc, broker_resolver):
            resolved = await resolver.resolve_access_token(at1)
            assert resolved is not None and resolved.active is True
            assert resolved.permissions == narrowed_expected

        assert (await token_svc.introspect(at1))["scope"] == " ".join(narrowed_expected)
        assert (await token_svc.introspect(rt1))["scope"] == " ".join(narrowed_expected)

        async with _http_client(control_app, at1) as ctrl_client:
            assert (await ctrl_client.get("/users")).status_code == 403
        async with _http_client(broker_app, at1) as broker_client:
            denied = await broker_client.post("/probe-execute")
            assert denied.status_code == 403
            assert denied.json()["type"] == "insufficient_scope"

        # Refresh rotation reports and persists only the narrowed scope set.
        async with _http_client(control_app) as client:
            refresh_resp = await client.post(
                "/oauth/token",
                json={"grant_type": "refresh_token", "refresh_token": rt1},
            )
        assert refresh_resp.status_code == 200, refresh_resp.text
        refresh_body = refresh_resp.json()
        assert refresh_body.get("scope") == " ".join(narrowed_expected)
        at2 = str(refresh_body["access_token"])
        rt2 = str(refresh_body["refresh_token"])

        for resolver in (token_svc, broker_resolver):
            resolved2 = await resolver.resolve_access_token(at2)
            assert resolved2 is not None and resolved2.permissions == narrowed_expected

        # Re-granting users:write later must not widen an already-narrowed refresh family.
        await PermissionService(ctx).set_assigned(
            user_id, [APIS_READ, USERS_WRITE], identity=admin_identity
        )
        at3, _rt3, reported3 = await token_svc.refresh(rt2)
        assert reported3 == narrowed_expected
        for resolver in (token_svc, broker_resolver):
            resolved3 = await resolver.resolve_access_token(at3)
            assert resolved3 is not None and resolved3.permissions == narrowed_expected
    finally:
        ctx.config.broker.resolve_cache_ttl_seconds = prior_ttl


@pytest.mark.asyncio
async def test_stale_db_snapshot_on_user_tokens_cannot_restore_unheld_permissions(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1442: pre-existing ``access_tokens`` / ``refresh_tokens`` rows carrying
    unheld permissions on ``scopes`` are capped by live ``user_permission_grants``."""
    ctx = local_login_ctx
    user_id, _ = await seed_password_user(ctx, "usr_1442_stale_row")

    raw_at = f"at_{secrets.token_urlsafe(32)}"
    raw_rt = f"rt_{secrets.token_urlsafe(32)}"
    now = datetime.now(UTC)
    async with ctx.admin_db.transaction() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=hashlib.sha256(raw_at.encode()).hexdigest(),
            actor_id=user_id,
            actor_type=ActorType.USER,
            scopes=["openid", BROKER_EXECUTE_PERMISSION, ORG_ADMIN],
            token_family_id="tfam_1442_stale",
            expires_at=now + timedelta(hours=1),
            created_by=SEED_MARKER,
        )
        await RefreshTokenRepository.create(
            session,
            token_hash=hashlib.sha256(raw_rt.encode()).hexdigest(),
            actor_id=user_id,
            actor_type=ActorType.USER,
            scopes=["openid", BROKER_EXECUTE_PERMISSION, ORG_ADMIN],
            token_family_id="tfam_1442_stale",
            expires_at=now + timedelta(days=7),
            created_by=SEED_MARKER,
        )

    control_app = _make_control_app(ctx)
    broker_app = _make_broker_probe_app(ctx)
    token_svc = TokenService(ctx)
    broker_resolver = InProcessTokenResolver(ctx.admin_db)

    for resolver in (token_svc, broker_resolver):
        resolved = await resolver.resolve_access_token(raw_at)
        assert resolved is not None and resolved.active is True
        assert resolved.permissions == ["openid"]

    async with _http_client(control_app, raw_at) as ctrl_client:
        assert (await ctrl_client.get("/users")).status_code == 403
    async with _http_client(broker_app, raw_at) as broker_client:
        assert (await broker_client.post("/probe-execute")).status_code == 403

    rotated_at, _rotated_rt, scopes = await token_svc.refresh(raw_rt)
    assert scopes == ["openid"]
    assert (await broker_resolver.resolve_access_token(rotated_at)).permissions == ["openid"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_spa_oidc_only_session_keeps_live_control_plane_permissions(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Pins #1442: an OIDC-only first-party SPA login (``openid email profile``)
    resolves the user's live permissions on the control plane and drops them on narrowing."""
    ctx = local_login_ctx
    admin_id, _ = await seed_password_user(ctx, "usr_1442_spa_admin")
    await seed_permissions(ctx, admin_id, [ORG_ADMIN])
    admin_identity = Identity(sub=admin_id, actor_type=ActorType.USER, permissions=[ORG_ADMIN])

    operator_id, operator_email = await seed_password_user(ctx, "usr_1442_spa_operator")
    await seed_permissions(ctx, operator_id, [USERS_READ])

    control_app = _make_control_app(ctx)
    broker_app = _make_broker_probe_app(ctx)

    spa_tokens = await _mint_platform_pkce_tokens(
        control_app,
        email=operator_email,
        scope="openid email profile",
    )
    assert spa_tokens.get("scope") == "openid email profile"
    spa_at = str(spa_tokens["access_token"])

    async with _http_client(control_app, spa_at) as ctrl_client:
        assert (await ctrl_client.get("/users")).status_code == 200
    async with _http_client(broker_app, spa_at) as broker_client:
        assert (await broker_client.post("/probe-execute")).status_code == 403

    await PermissionService(ctx).set_assigned(operator_id, [], identity=admin_identity)
    async with _http_client(control_app, spa_at) as ctrl_client:
        assert (await ctrl_client.get("/users")).status_code == 403


@pytest.mark.asyncio
async def test_empty_scope_request_keeps_the_oidc_session_default(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """An omitted scope still creates a first-party OIDC session with live grants."""
    ctx = local_login_ctx
    user_id, _ = await seed_password_user(ctx, "usr_1442_empty_scope")
    await seed_permissions(ctx, user_id, [USERS_READ])
    authorize_svc = AuthorizeService(ctx)
    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes="",
    )

    access, _refresh, id_token, scopes = await authorize_svc.exchange_code(
        code=code,
        code_verifier=CODE_VERIFIER,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    )

    assert id_token is not None
    assert scopes == ["openid"]
    control_app = _make_control_app(ctx)
    async with _http_client(control_app, access) as client:
        assert (await client.get("/users")).status_code == 200


@pytest.mark.asyncio
async def test_legacy_empty_scope_code_keeps_the_oidc_session_default(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Legacy rows with an empty scope string retain their OIDC-only session meaning."""
    ctx = local_login_ctx
    user_id, _ = await seed_password_user(ctx, "usr_1442_legacy_empty_scope")
    await seed_permissions(ctx, user_id, [USERS_READ])
    raw_code = secrets.token_urlsafe(32)
    async with ctx.admin_db.transaction() as session:
        await AuthorizationCodeRepository.create(
            session,
            code_hash=hashlib.sha256(raw_code.encode()).hexdigest(),
            user_id=user_id,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
            code_challenge=code_challenge(CODE_VERIFIER),
            scopes="",
            nonce=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            created_by=SEED_MARKER,
        )

    access, _refresh, id_token, scopes = await AuthorizeService(ctx).exchange_code(
        code=raw_code,
        code_verifier=CODE_VERIFIER,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    )

    assert id_token is not None
    assert scopes == ["openid"]
    control_app = _make_control_app(ctx)
    async with _http_client(control_app, access) as client:
        assert (await client.get("/users")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_scopes", [" ", "  "])
async def test_legacy_whitespace_scope_code_stays_empty(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
    stored_scopes: str,
) -> None:
    """Nonempty whitespace keeps its legacy zero-scope meaning instead of defaulting to OIDC."""
    ctx = local_login_ctx
    user_id, _ = await seed_password_user(ctx, f"usr_1442_legacy_ws_{len(stored_scopes)}")
    await seed_permissions(ctx, user_id, [USERS_READ])
    raw_code = secrets.token_urlsafe(32)
    async with ctx.admin_db.transaction() as session:
        await AuthorizationCodeRepository.create(
            session,
            code_hash=hashlib.sha256(raw_code.encode()).hexdigest(),
            user_id=user_id,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
            code_challenge=code_challenge(CODE_VERIFIER),
            scopes=stored_scopes,
            nonce=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            created_by=SEED_MARKER,
        )

    access, _refresh, _id_token, scopes = await AuthorizeService(ctx).exchange_code(
        code=raw_code,
        code_verifier=CODE_VERIFIER,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    )

    assert scopes == []
    control_app = _make_control_app(ctx)
    async with _http_client(control_app, access) as client:
        assert (await client.get("/users")).status_code == 403


@pytest.mark.asyncio
async def test_permission_only_request_narrowed_to_zero_stays_empty(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """A denied permission request must not turn into a live-grant OIDC session."""
    ctx = local_login_ctx
    user_id, _ = await seed_password_user(ctx, "usr_1442_narrowed_empty_scope")
    await seed_permissions(ctx, user_id, [USERS_READ])
    authorize_svc = AuthorizeService(ctx)
    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=BROKER_EXECUTE_PERMISSION,
    )
    async with ctx.admin_db.session() as session:
        stored = await AuthorizationCodeRepository.get_by_hash(
            session, hashlib.sha256(code.encode()).hexdigest()
        )
        assert stored is not None
        assert stored.scopes == BROKER_EXECUTE_PERMISSION

    access, _refresh, _id_token, scopes = await authorize_svc.exchange_code(
        code=code,
        code_verifier=CODE_VERIFIER,
        redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    )

    assert scopes == []
    resolved = await TokenService(ctx).resolve_access_token(access)
    assert resolved is not None and resolved.permissions == []
    control_app = _make_control_app(ctx)
    async with _http_client(control_app, access) as client:
        assert (await client.get("/users")).status_code == 403


@pytest.mark.asyncio
async def test_third_party_client_and_agent_ceilings_remain_distinct(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Third-party ceilings cap USER permissions without dropping OIDC/offline
    passthrough scopes, and agent grants stay isolated to AGENT tokens."""
    ctx = local_login_ctx
    user_id, email = await seed_password_user(ctx, "usr_1442_ceilings")
    await seed_permissions(ctx, user_id, [APIS_READ, USERS_READ])

    async with ctx.admin_db.session() as session:
        await OAuthClientRepository.create(
            session,
            client_id=_USER_CLIENT_ID,
            name="User Ceiling Client",
            redirect_uris=[_USER_CLIENT_REDIRECT],
            client_secret_hash=None,
            allowed_scopes=["openid", APIS_READ],
            token_endpoint_auth_method="none",
            consent_model="user",
            created_by=SEED_MARKER,
        )
        await session.commit()

    # Agent owned by the user holds capabilities:execute via actor_permission_grants,
    # which must NEVER bleed into the owning USER's own tokens.
    agent_id = await seed_agent(
        ctx, owner_id=user_id, scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ]
    )
    await seed_client(ctx, allowed_scopes=[BROKER_EXECUTE_PERMISSION, APIS_READ])
    _grant_id, agent_at, _agent_rt, _ = await mint_grant_channel_tokens(
        ctx,
        user_id=user_id,
        agent_id=agent_id,
        grant_scopes=[BROKER_EXECUTE_PERMISSION],
    )

    control_app = _make_control_app(ctx)
    broker_app = _make_broker_probe_app(ctx)

    # Agent token still resolves from actor_permission_grants and passes broker execute.
    async with _http_client(broker_app, agent_at) as broker_client:
        assert (await broker_client.post("/probe-execute")).status_code == 200

    # User's own platform token cannot draw capabilities:execute from owned agent's grants.
    user_platform = await _mint_platform_pkce_tokens(
        control_app,
        email=email,
        scope=f"openid {APIS_READ} {BROKER_EXECUTE_PERMISSION}",
    )
    assert user_platform.get("scope") == f"openid {APIS_READ}"
    async with _http_client(broker_app, str(user_platform["access_token"])) as broker_client:
        assert (await broker_client.post("/probe-execute")).status_code == 403

    # Registered USER clients cap permissions but retain the OIDC/offline scopes
    # accepted by the authorize endpoint throughout the token lifecycle.
    authorize_svc = AuthorizeService(ctx)
    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=_USER_CLIENT_ID,
        redirect_uri=_USER_CLIENT_REDIRECT,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=(
            f"openid email offline_access {APIS_READ} {USERS_READ} {BROKER_EXECUTE_PERMISSION}"
        ),
    )
    with patch(
        "jentic_one.auth.services.authorize_service.issue_id_token",
        return_value="hdr.payload.sig",
    ):
        user_at, user_rt, id_token, user_scopes = await authorize_svc.exchange_code(
            code=code,
            code_verifier=CODE_VERIFIER,
            redirect_uri=_USER_CLIENT_REDIRECT,
            client_id=_USER_CLIENT_ID,
            oauth_client_id=_USER_CLIENT_ID,
        )

    expected_user_scopes = ["openid", "email", "offline_access", APIS_READ]
    assert id_token == "hdr.payload.sig"
    assert user_scopes == expected_user_scopes

    token_svc = TokenService(ctx)
    broker_resolver = InProcessTokenResolver(ctx.admin_db)
    for resolver in (token_svc, broker_resolver):
        resolved = await resolver.resolve_access_token(user_at)
        assert resolved is not None and resolved.permissions == expected_user_scopes
    assert (await token_svc.introspect(user_at))["scope"] == " ".join(expected_user_scopes)
    assert (await token_svc.introspect(user_rt))["scope"] == " ".join(expected_user_scopes)

    rotated_at, _rotated_rt, rotated_scopes = await token_svc.refresh(
        user_rt, client_id=_USER_CLIENT_ID
    )
    assert rotated_scopes == expected_user_scopes
    for resolver in (token_svc, broker_resolver):
        rotated = await resolver.resolve_access_token(rotated_at)
        assert rotated is not None and rotated.permissions == expected_user_scopes
