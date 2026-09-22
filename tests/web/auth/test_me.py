"""Tests for GET /me identity endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.service_account_credentials import ServiceAccountCredential
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentCredentialBindingRepository,
    AgentRepository,
    AgentToolkitBindingRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.auth.services.crypto import hash_secret
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.app import create_app
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, InviteState
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "me-test-admin@test.local"
OWNER_EMAIL = "me-test-owner@test.local"

# A real toolkit (control DB) the agent is bound to — /me must resolve its name
# (issue #686). ``tk_me_orphan`` is a binding with no toolkit row, exercising the
# graceful name=None path.
NAMED_TOOLKIT_ID = "tk_me_named"
NAMED_TOOLKIT_NAME = "Design news radar"
ORPHAN_TOOLKIT_ID = "tk_me_orphan"

# A real credential (control DB) the agent is directly bound to (theme 5
# phase 1) — /me must resolve its name and served API. ``cred_me_orphan`` is a
# binding with no credential row, exercising the graceful name=None path.
NAMED_CREDENTIAL_ID = "cred_me_named"
NAMED_CREDENTIAL_NAME = "Stripe live account"
ORPHAN_CREDENTIAL_ID = "cred_me_orphan"


def _build_app(ctx: Context) -> FastAPI:
    """Build the auth app using the real factory, with lifespan disabled."""
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    return app


def _make_token(
    ctx: Context,
    sub: str,
    email: str,
    permissions: list[str],
    *,
    must_change_password: bool = False,
    actor_type: str = "user",
    parent_actor_id: str | None = None,
) -> str:
    config = ctx.config.admin.auth
    claims = {
        "sub": sub,
        "email": email,
        "scopes": permissions,
        "actor_type": actor_type,
        "must_change_password": must_change_password,
    }
    if parent_actor_id:
        claims["parent_actor_id"] = parent_actor_id
    # The ``scopes`` claim (not ``permissions``) is deliberate: ``verify_token``
    # folds OAuth2 scopes into the permission set *on top of* the DB-resolved
    # grants, whereas a ``permissions`` claim short-circuits the lookup. The #673
    # tests below need both legs — the token's baked-in set and the live grants.
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


@pytest.fixture()
async def admin_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=ADMIN_EMAIL,
            first_name="Me",
            last_name="Admin",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("test-password-123"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"org:admin"}, granted_by=None, created_by="usr_test"
        )
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(Agent).where(Agent.owner_id == user.id))
        await session.execute(delete(ServiceAccount).where(ServiceAccount.owner_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
async def owner_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=OWNER_EMAIL,
            first_name="Owner",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("test-password-123"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session,
            user.id,
            permissions={
                "agents:read",
                "agents:write",
            },
            granted_by=None,
            created_by="usr_test",
        )
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(Agent).where(Agent.owner_id == user.id))
        await session.execute(delete(ServiceAccount).where(ServiceAccount.owner_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
async def approved_agent_id(
    web_context: Context, owner_user_id: str, admin_user_id: str
) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="me-test-agent",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            description="Agent for /me tests",
            created_by="usr_test",
        )
        await AgentRepository.set_approval(session, agent.id, approved_by=admin_user_id)
        await AgentToolkitBindingRepository.bind(
            session, agent_id=agent.id, toolkit_id=NAMED_TOOLKIT_ID, created_by="usr_test"
        )
        await AgentToolkitBindingRepository.bind(
            session, agent_id=agent.id, toolkit_id=ORPHAN_TOOLKIT_ID, created_by="usr_test"
        )
        # Direct credential bindings (theme 5 phase 1): one resolvable, one
        # orphaned — mirrors the named/orphan toolkit pair above.
        await AgentCredentialBindingRepository.bind(
            session, agent_id=agent.id, credential_id=NAMED_CREDENTIAL_ID, created_by="usr_test"
        )
        await AgentCredentialBindingRepository.bind(
            session, agent_id=agent.id, credential_id=ORPHAN_CREDENTIAL_ID, created_by="usr_test"
        )
        # A live grant the presented token won't carry — exercises #673: /me must
        # reflect current grants, not just the token's baked-in permissions.
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent.id,
            actor_type="agent",
            permission="capabilities:read",
            granted_by=admin_user_id,
            created_by="usr_test",
        )
    # The toolkit name lives in the control DB; seed a real row so /me can resolve
    # NAMED_TOOLKIT_ID → its name (issue #686). ORPHAN_TOOLKIT_ID has no row.
    async with ctx.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, created_by) "
                "VALUES (:id, :name, :created_by) ON CONFLICT DO NOTHING"
            ),
            {"id": NAMED_TOOLKIT_ID, "name": NAMED_TOOLKIT_NAME, "created_by": owner_user_id},
        )
        # Same for the credential row backing the direct binding; raw SQL keeps
        # this file free of control-ORM imports (same convention as toolkits).
        # ORPHAN_CREDENTIAL_ID has no row.
        await session.execute(
            text(
                "INSERT INTO credentials "
                "(id, type, name, api_vendor, api_name, api_version, created_by) "
                "VALUES (:id, 'token_value', :name, 'stripe', 'payments', 'v1', :created_by) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": NAMED_CREDENTIAL_ID,
                "name": NAMED_CREDENTIAL_NAME,
                "created_by": owner_user_id,
            },
        )
        await session.commit()
    yield agent.id

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(AgentToolkitBinding).where(AgentToolkitBinding.agent_id == agent.id)
        )
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == agent.id)
        )
        await ActorPermissionGrantRepository.revoke_all(session, agent.id)
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()
    async with ctx.control_db.session() as session:
        await session.execute(text("DELETE FROM toolkits WHERE id = :id"), {"id": NAMED_TOOLKIT_ID})
        await session.execute(
            text("DELETE FROM credentials WHERE id = :id"), {"id": NAMED_CREDENTIAL_ID}
        )
        await session.commit()


# An unmigrated service account's API key (theme-8 Phase 2): the SA surface is
# gone, but until the Phase-4 drop the resolver's SA-table fallback still
# resolves an unmigrated ``sak_`` key as the SA, and /me must answer for it.
UNMIGRATED_SAK_KEY = "sak_me_unmigrated_fallback_key"


@pytest.fixture()
async def approved_sa_id(
    web_context: Context, owner_user_id: str, admin_user_id: str
) -> AsyncGenerator[str, None]:
    """An active, unmigrated SA with a ``sak_`` digest and one live grant.

    Seeded through the ORM directly — the SA repositories were deleted with the
    surface; the models survive until the Phase-4 drop.
    """
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        sa = ServiceAccount(
            name="me-test-sa",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            approved_by=admin_user_id,
            description="SA for /me tests",
            status="active",
            created_by="usr_test",
        )
        session.add(sa)
        await session.flush()
        session.add(
            ServiceAccountCredential(
                service_account_id=sa.id,
                api_key_hash=hash_secret(UNMIGRATED_SAK_KEY),
                created_by="usr_test",
            )
        )
        # A live permission grant — /me must reflect current grants (#673).
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=sa.id,
            actor_type="service_account",
            permission="capabilities:read",
            granted_by=admin_user_id,
            created_by="usr_test",
        )
        sa_id = sa.id
    yield sa_id

    async with ctx.admin_db.session() as session:
        await ActorPermissionGrantRepository.revoke_all(session, sa_id)
        await session.execute(
            delete(ServiceAccountCredential).where(
                ServiceAccountCredential.service_account_id == sa_id
            )
        )
        await session.execute(delete(ServiceAccount).where(ServiceAccount.id == sa_id))
        await session.commit()


def test_me_user_admin(web_context: Context, admin_user_id: str) -> None:
    token = _make_token(web_context, admin_user_id, ADMIN_EMAIL, ["org:admin"])
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "user"
    assert body["id"] == admin_user_id
    assert body["email"] == ADMIN_EMAIL
    assert body["name"] == "Me Admin"
    assert body["admin"] is True
    assert "org:admin" in body["permissions"]
    assert body["status"] == "active"
    assert body["must_change_password"] is False


def test_me_user_owner(web_context: Context, owner_user_id: str) -> None:
    token = _make_token(
        web_context,
        owner_user_id,
        OWNER_EMAIL,
        ["agents:read", "agents:write"],
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "user"
    assert body["id"] == owner_user_id
    assert body["admin"] is False
    assert "agents:read" in body["permissions"]
    assert body["status"] == "active"


def test_me_agent(web_context: Context, approved_agent_id: str) -> None:
    token = _make_token(
        web_context,
        approved_agent_id,
        "agent@internal",
        ["toolkits:execute"],
        actor_type="agent",
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "agent"
    assert body["id"] == approved_agent_id
    assert body["name"] == "me-test-agent"
    assert body["status"] == "active"
    # permissions reflect the *live* actor_permission_grants (the grant from the
    # fixture), not the token's baked-in set — this is the #673 fix.
    # token_permissions carries the presented token's view so a stale-grant gap
    # is detectable.
    assert body["permissions"] == ["capabilities:read"]
    assert body["token_permissions"] == ["toolkits:execute"]
    assert body["parent_agent_id"] is None
    assert body["approved_by"] is not None
    bindings = {b["toolkit_id"]: b for b in body["toolkit_bindings"]}
    assert len(bindings) == 2
    # The bound toolkit's human-readable name is resolved from the control DB so
    # the agent can map the opaque id to a name (issue #686)…
    assert bindings[NAMED_TOOLKIT_ID]["name"] == NAMED_TOOLKIT_NAME
    # …while a binding whose toolkit row is absent degrades gracefully to null
    # rather than failing the whole response.
    assert bindings[ORPHAN_TOOLKIT_ID]["name"] is None
    # Direct credential bindings (theme 5 phase 1) mirror the same contract:
    # resolvable name + served API for the real credential…
    cred_bindings = {b["credential_id"]: b for b in body["credential_bindings"]}
    assert len(cred_bindings) == 2
    named = cred_bindings[NAMED_CREDENTIAL_ID]
    assert named["name"] == NAMED_CREDENTIAL_NAME
    assert named["suspended"] is False
    # No shared rule set attached — inline rules govern this binding (Q-04).
    assert named["rule_set_id"] is None
    assert named["serves"] == [
        {"api_vendor": "stripe", "api_name": "payments", "api_version": "v1"}
    ]
    # …and graceful degradation (name=None, empty serves) for the orphan.
    orphan = cred_bindings[ORPHAN_CREDENTIAL_ID]
    assert orphan["name"] is None
    assert orphan["serves"] == []


async def test_me_agent_opaque_token_surfaces_minted_scopes(
    web_context: Context, approved_agent_id: str
) -> None:
    """Regression: an opaque agent access token (``at_…``) must surface the scopes
    minted onto its ``access_tokens`` row as the agent's token permissions.

    The auth verifier (``_make_auth_verifier``) used to discard the token-row
    scopes for agents and recompute via ``resolve_permissions_for_actor``, whose
    AGENT branch is an unimplemented stub that returns ``[]`` — so an approved
    ``capabilities:read`` never took effect and re-minting the token could
    not help. Unlike the JWT path in ``test_me_agent`` (which falls back to the
    token's ``scopes`` claim), the opaque-token path has no such claim, and it is
    the path real CLI agents use after the jwt-bearer exchange. ``token_permissions`` must
    therefore echo the minted scopes, not come back empty.
    """
    token_svc = TokenService(web_context)
    access_token, _refresh = await token_svc.issue_pair(
        approved_agent_id, ActorType.AGENT, ["capabilities:read"]
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {access_token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "agent"
    assert body["id"] == approved_agent_id
    # The presented opaque token carries capabilities:read on its row; the
    # verifier must surface it (pre-fix this came back [] and every
    # capabilities:read-gated call 403'd).
    assert body["token_permissions"] == ["capabilities:read"]
    # permissions still reflects the live actor_permission_grants.
    assert body["permissions"] == ["capabilities:read"]


def test_me_service_account_fallback_resolved_key(
    web_context: Context, approved_sa_id: str, owner_user_id: str
) -> None:
    """Theme-8 Phase 2 (M-1): an unmigrated ``sak_`` key resolves through the
    SA-table fallback, and /me answers coherently from the shared raw-SQL read
    (the deleted ``ServiceAccountService`` is not involved)."""
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {UNMIGRATED_SAK_KEY}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "service_account"
    assert body["id"] == approved_sa_id
    assert body["name"] == "me-test-sa"
    assert body["status"] == "active"
    # `permissions` = live grants; `token_permissions` = what the resolver loaded
    # for the key (the same live grants on the API-key path).
    assert body["permissions"] == ["capabilities:read"]
    assert body["token_permissions"] == ["capabilities:read"]
    assert body["registered_by"] == owner_user_id
    assert body["approved_by"] is not None


def test_me_service_account_jwt_subject(
    web_context: Context, approved_sa_id: str, owner_user_id: str
) -> None:
    """A (historical) ``sva_`` JWT subject still gets a coherent /me answer."""
    token = _make_token(
        web_context,
        approved_sa_id,
        "sa@internal",
        ["registry:read"],
        actor_type="service_account",
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "service_account"
    assert body["id"] == approved_sa_id
    assert body["permissions"] == ["capabilities:read"]
    assert body["token_permissions"] == ["registry:read"]


def test_me_service_account_row_gone_is_401(web_context: Context) -> None:
    """An ``sva_`` subject with no row fails closed (401), never a 500."""
    token = _make_token(
        web_context, "sva_me_missing_row", "sa@internal", [], actor_type="service_account"
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 401


def test_me_unauthenticated(web_context: Context) -> None:
    app = _build_app(web_context)
    with TestClient(app) as client:
        resp = client.get("/me")
    assert resp.status_code == 401


def test_me_invalid_token(web_context: Context) -> None:
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": "Bearer invalid.token.here"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 401


def test_me_unknown_prefix(web_context: Context) -> None:
    token = _make_token(web_context, "xyz_12345", "unknown@test.local", ["some:perm"])
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 401
