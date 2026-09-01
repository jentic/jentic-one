"""Integration tests for the phase-3a grant channel (design §4.4-§4.6, §9).

Covers the consent→agent binding end-to-end at the service layer: grant
minting with the D2 scope intersection, grant-bearing code exchange minting
actor=AGENT with both lineage columns and no id_token (D11), the quadruple
scope intersection at resolution, refresh-rotation grant re-checks, and all
three kill radii (grant revoke / client deactivate / agent disable) on BOTH
resolvers (auth-surface TokenService + broker raw-SQL resolver).
"""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ActorScopeGrantRepository,
    AgentRepository,
    OAuthClientGrantRepository,
    OAuthClientRepository,
    UserRepository,
)
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import (
    InvalidGrantError,
    OAuthGrantAccessDeniedError,
    OAuthGrantNotFoundError,
)
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_grant_seed"
_CLIENT_ID = "oc_grant_channel_test"
_REDIRECT_URI = "https://mcpapp.example.com/cb"
_CODE_VERIFIER = "grant-channel-verifier-0123456789abcdef0123456789abcdef"


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.fixture()
async def clean_grants(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AccessToken))
            await session.execute(delete(RefreshToken))
            await session.execute(delete(AuthorizationCode))
            await session.execute(delete(OAuthClientGrant))
            await session.execute(delete(ActorScopeGrant))
            await session.execute(delete(OAuthClient).where(OAuthClient.created_by == _SEED_MARKER))
            await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
            await session.execute(delete(User).where(User.created_by == _SEED_MARKER))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_user(ctx: Context, user_id: str) -> str:
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            id=user_id,
            email=f"{user_id}@grants.test",
            first_name="Grant",
            last_name="Test",
            active=True,
            created_by=_SEED_MARKER,
        )
        await session.commit()
        return user.id


async def _seed_agent(
    ctx: Context,
    *,
    owner_id: str,
    scopes: list[str],
    status: ActorStatus = ActorStatus.ACTIVE,
) -> str:
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="grant-test-agent",
            owner_id=owner_id,
            registered_by=owner_id,
            created_by=_SEED_MARKER,
            status=status,
        )
        for scope in scopes:
            await ActorScopeGrantRepository.grant(
                session,
                actor_id=agent.id,
                actor_type=ActorType.AGENT,
                scope=scope,
                granted_by=owner_id,
                created_by=_SEED_MARKER,
            )
        await session.commit()
        return agent.id


async def _seed_client(
    ctx: Context,
    *,
    allowed_scopes: list[str] | None,
    client_id: str = _CLIENT_ID,
) -> str:
    async with ctx.admin_db.session() as session:
        client = await OAuthClientRepository.create(
            session,
            client_id=client_id,
            name="Grant Channel App",
            redirect_uris=[_REDIRECT_URI],
            client_secret_hash=None,
            allowed_scopes=allowed_scopes,
            token_endpoint_auth_method="none",
            consent_model="agent",
            registration_source="dcr",
            created_by=_SEED_MARKER,
        )
        await session.commit()
        return client.client_id


async def _mint_grant_channel_tokens(
    ctx: Context,
    *,
    user_id: str,
    agent_id: str,
    grant_scopes: list[str],
) -> tuple[str, str, str, str | None]:
    """Consent-approve + code issue + exchange. Returns (grant_id, at, rt, id_token)."""
    grant_svc = OAuthGrantService(ctx)
    authorize_svc = AuthorizeService(ctx)
    grant_id = await grant_svc.create_grant(
        user_id=user_id,
        oauth_client_id=_CLIENT_ID,
        agent_id=agent_id,
        scopes=grant_scopes,
        client_name="Grant Channel App",
    )
    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        code_challenge=_code_challenge(_CODE_VERIFIER),
        scopes=" ".join(grant_scopes),
        grant_id=grant_id,
    )
    access, refresh, id_token = await authorize_svc.exchange_code(
        code=code,
        code_verifier=_CODE_VERIFIER,
        redirect_uri=_REDIRECT_URI,
        client_id=_CLIENT_ID,
        oauth_client_id=_CLIENT_ID,
    )
    return grant_id, access, refresh, id_token


# --- grant table + minting -------------------------------------------------


async def test_grant_row_defaults_and_collapse(
    integration_context: Context, clean_grants: None
) -> None:
    """Grant defaults (ksuid `ocg`, status=active, timestamps) + the §4.1
    pair-collapse: re-consent revokes the prior active row for the pair."""
    user_id = await _seed_user(integration_context, "usr_g_defaults")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)

    grant_id = await grant_svc.create_grant(
        user_id=user_id,
        oauth_client_id=_CLIENT_ID,
        agent_id=agent_id,
        scopes=["apis:read"],
    )
    grant = await grant_svc.get_grant(grant_id)
    assert grant is not None
    assert grant.id.startswith("ocg_")
    assert grant.status == "active"
    assert grant.revoked_at is None
    assert grant.last_used_at is None
    assert grant.created_at is not None
    assert list(grant.scopes) == ["apis:read"]
    assert grant.created_by == user_id

    # Re-consent for the same (client, agent) pair collapses the old row.
    grant_id_2 = await grant_svc.create_grant(
        user_id=user_id,
        oauth_client_id=_CLIENT_ID,
        agent_id=agent_id,
        scopes=["apis:read"],
    )
    old = await grant_svc.get_grant(grant_id)
    new = await grant_svc.get_grant(grant_id_2)
    assert old is not None and old.status == "revoked" and old.revoked_at is not None
    assert new is not None and new.status == "active"
    async with integration_context.admin_db.session() as session:
        active_pair = await OAuthClientGrantRepository.list_active_for_pair(
            session, oauth_client_id=_CLIENT_ID, agent_id=agent_id
        )
    assert [g.id for g in active_pair] == [grant_id_2]


# --- exchange (grant channel) ----------------------------------------------


async def test_grant_exchange_mints_agent_actor_with_lineage_and_no_id_token(
    integration_context: Context, clean_grants: None
) -> None:
    """§4.5: a grant-bearing code mints actor=AGENT with BOTH lineage columns
    stamped, no id_token (D11), and touches the grant's last_used_at."""
    user_id = await _seed_user(integration_context, "usr_g_exchange")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])

    grant_id, access, refresh, id_token = await _mint_grant_channel_tokens(
        integration_context, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    assert id_token is None, "agent-bound exchange must not mint an id_token (D11)"

    token_svc = TokenService(integration_context)
    resolved = await token_svc.resolve_access_token(access)
    assert resolved is not None
    assert resolved.active is True
    assert resolved.sub == agent_id
    assert resolved.actor_type == ActorType.AGENT
    assert resolved.oauth_client_id == _CLIENT_ID
    assert resolved.oauth_grant_id == grant_id
    assert resolved.permissions == ["apis:read"]

    # Refresh row carries the same lineage; last_used_at was touched at mint.
    async with integration_context.admin_db.session() as session:
        rt_rows = (await session.execute(RefreshToken.__table__.select())).all()
    assert len(rt_rows) == 1
    assert rt_rows[0].oauth_client_id == _CLIENT_ID
    assert rt_rows[0].oauth_grant_id == grant_id

    grant = await OAuthGrantService(integration_context).get_grant(grant_id)
    assert grant is not None and grant.last_used_at is not None

    # The code is single-use.
    with pytest.raises(InvalidGrantError):
        await AuthorizeService(integration_context).exchange_code(
            code="nonexistent",
            code_verifier=_CODE_VERIFIER,
            redirect_uri=_REDIRECT_URI,
            client_id=_CLIENT_ID,
            oauth_client_id=_CLIENT_ID,
        )
    _ = refresh


async def test_plain_code_keeps_user_actor_and_id_token(
    integration_context: Context, clean_grants: None
) -> None:
    """Codes without grant_id keep the act-as-user path with an id_token —
    the consent_model='user' contract stays byte-identical. (The integration
    config carries no ES256 signing keys, so the issuer is patched: the pin
    is that the user path *mints* an id_token while the grant path returns
    None.)"""
    user_id = await _seed_user(integration_context, "usr_g_userpath")
    await _seed_client(integration_context, allowed_scopes=None)
    authorize_svc = AuthorizeService(integration_context)

    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        code_challenge=_code_challenge(_CODE_VERIFIER),
        scopes="openid email",
    )
    with patch(
        "jentic_one.auth.services.authorize_service.issue_id_token",
        return_value="hdr.payload.sig",
    ) as mock_issue:
        access, _refresh, id_token = await authorize_svc.exchange_code(
            code=code,
            code_verifier=_CODE_VERIFIER,
            redirect_uri=_REDIRECT_URI,
            client_id=_CLIENT_ID,
            oauth_client_id=_CLIENT_ID,
        )
    assert id_token == "hdr.payload.sig"
    mock_issue.assert_called_once()

    token_svc = TokenService(integration_context)
    resolved = await token_svc.resolve_access_token(access)
    assert resolved is not None
    assert resolved.sub == user_id
    assert resolved.actor_type == ActorType.USER
    assert resolved.oauth_grant_id is None


async def test_exchange_fails_closed_on_revoked_grant_or_inactive_agent(
    integration_context: Context, clean_grants: None
) -> None:
    """§4.5: every leg is re-checked at exchange — a grant revoked (or agent
    disabled) inside the code TTL must not mint tokens."""
    user_id = await _seed_user(integration_context, "usr_g_exch_gate")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)
    authorize_svc = AuthorizeService(integration_context)

    grant_id = await grant_svc.create_grant(
        user_id=user_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )
    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        code_challenge=_code_challenge(_CODE_VERIFIER),
        scopes="apis:read",
        grant_id=grant_id,
    )
    await grant_svc.revoke_grant(grant_id, identity=Identity(sub=user_id, email=""))

    with pytest.raises(InvalidGrantError, match="not active"):
        await authorize_svc.exchange_code(
            code=code,
            code_verifier=_CODE_VERIFIER,
            redirect_uri=_REDIRECT_URI,
            client_id=_CLIENT_ID,
            oauth_client_id=_CLIENT_ID,
        )


# --- resolution: the quadruple intersection ---------------------------------


async def test_resolution_honors_quadruple_scope_intersection(
    integration_context: Context, clean_grants: None
) -> None:
    """§4.5: effective scopes = token snapshot ∩ agent live grants ∩ client
    ceiling ∩ grant scopes — on both resolvers. Narrowing the agent's live
    grants after mint narrows the effective set without a re-mint."""
    user_id = await _seed_user(integration_context, "usr_g_quad")
    agent_id = await _seed_agent(
        integration_context, owner_id=user_id, scopes=["apis:read", "apis:write"]
    )
    await _seed_client(integration_context, allowed_scopes=["apis:read", "apis:write"])

    grant_id, access, _refresh, _ = await _mint_grant_channel_tokens(
        integration_context,
        user_id=user_id,
        agent_id=agent_id,
        grant_scopes=["apis:read", "apis:write"],
    )

    token_svc = TokenService(integration_context)
    broker = InProcessTokenResolver(integration_context.admin_db)
    for resolver in (token_svc, broker):
        resolved = await resolver.resolve_access_token(access)
        assert resolved is not None and resolved.active is True
        assert resolved.permissions == ["apis:read", "apis:write"]
        assert resolved.oauth_grant_id == grant_id

    # Narrow the agent's live grants: apis:write revoked → drops immediately.
    async with integration_context.admin_db.session() as session:
        await ActorScopeGrantRepository.revoke(session, actor_id=agent_id, scope="apis:write")
        await session.commit()

    for resolver in (token_svc, broker):
        resolved = await resolver.resolve_access_token(access)
        assert resolved is not None and resolved.active is True
        assert resolved.permissions == ["apis:read"]


async def test_grant_scopes_cap_agent_live_scopes(
    integration_context: Context, clean_grants: None
) -> None:
    """The grant leg caps the intersection: scopes the agent gains *after*
    consent never leak into a grant-channel token (consent granted a fixed
    set, D2)."""
    user_id = await _seed_user(integration_context, "usr_g_cap")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read", "apis:write"])

    _grant_id, access, _refresh, _ = await _mint_grant_channel_tokens(
        integration_context, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    # Agent gains apis:write post-consent — the grant must still cap it out.
    async with integration_context.admin_db.session() as session:
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=agent_id,
            actor_type=ActorType.AGENT,
            scope="apis:write",
            granted_by=user_id,
            created_by=_SEED_MARKER,
        )
        await session.commit()

    token_svc = TokenService(integration_context)
    broker = InProcessTokenResolver(integration_context.admin_db)
    for resolver in (token_svc, broker):
        resolved = await resolver.resolve_access_token(access)
        assert resolved is not None
        assert resolved.permissions == ["apis:read"]


# --- the three kill radii (§4.6), on BOTH resolvers -------------------------


async def test_kill_radius_grant_revoke(integration_context: Context, clean_grants: None) -> None:
    """Radius 1: grant :revoke → sweep + live gate kill the token on both
    resolvers; refresh fails closed; introspection reports inactive."""
    user_id = await _seed_user(integration_context, "usr_g_kill1")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_id, access, refresh, _ = await _mint_grant_channel_tokens(
        integration_context, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    token_svc = TokenService(integration_context)
    broker = InProcessTokenResolver(integration_context.admin_db)
    assert (await token_svc.resolve_access_token(access)) is not None

    await OAuthGrantService(integration_context).revoke_grant(
        grant_id, identity=Identity(sub=user_id, email="")
    )

    # The sweep stamped revoked_at on every oauth_grant_id token row.
    async with integration_context.admin_db.session() as session:
        at_rows = (await session.execute(AccessToken.__table__.select())).all()
        rt_rows = (await session.execute(RefreshToken.__table__.select())).all()
    assert all(r.revoked_at is not None for r in at_rows)
    assert all(r.revoked_at is not None for r in rt_rows)

    assert await token_svc.resolve_access_token(access) is None
    broker_resolved = await broker.resolve_access_token(access)
    assert broker_resolved is not None and broker_resolved.active is False
    assert (await token_svc.introspect(access))["active"] is False
    with pytest.raises(InvalidGrantError):
        await token_svc.refresh(refresh, client_id=_CLIENT_ID)


async def test_kill_radius_client_deactivate(
    integration_context: Context, clean_grants: None
) -> None:
    """Radius 2: deactivating the client app kills grant-channel tokens on
    both resolvers (the existing live client gate)."""
    user_id = await _seed_user(integration_context, "usr_g_kill2")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    _grant_id, access, refresh, _ = await _mint_grant_channel_tokens(
        integration_context, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    async with integration_context.admin_db.session() as session:
        client = await OAuthClientRepository.get_by_client_id(session, _CLIENT_ID)
        assert client is not None
        await OAuthClientRepository.deactivate(session, client.id)
        await session.commit()

    token_svc = TokenService(integration_context)
    broker = InProcessTokenResolver(integration_context.admin_db)
    assert await token_svc.resolve_access_token(access) is None
    broker_resolved = await broker.resolve_access_token(access)
    assert broker_resolved is not None and broker_resolved.active is False
    with pytest.raises(InvalidGrantError, match="deactivated"):
        await token_svc.refresh(refresh, client_id=_CLIENT_ID)


async def test_kill_radius_agent_disable(integration_context: Context, clean_grants: None) -> None:
    """Radius 3: disabling the bound agent kills grant-channel tokens on both
    resolvers (the #1136/#1137 actor-status gate)."""
    user_id = await _seed_user(integration_context, "usr_g_kill3")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    _grant_id, access, refresh, _ = await _mint_grant_channel_tokens(
        integration_context, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    async with integration_context.admin_db.session() as session:
        await AgentRepository.update_status(session, agent_id, ActorStatus.DISABLED)
        await session.commit()

    token_svc = TokenService(integration_context)
    broker = InProcessTokenResolver(integration_context.admin_db)
    resolved = await token_svc.resolve_access_token(access)
    assert resolved is not None and resolved.active is False
    broker_resolved = await broker.resolve_access_token(access)
    assert broker_resolved is not None and broker_resolved.active is False
    with pytest.raises(InvalidGrantError, match="not active"):
        await token_svc.refresh(refresh, client_id=_CLIENT_ID)


# --- refresh re-check + revoke authorization --------------------------------


async def test_refresh_rotation_recheck_and_lineage_propagation(
    integration_context: Context, clean_grants: None
) -> None:
    """Refresh rotates normally while the grant is active (lineage columns
    propagate to the new pair); after revoke the rotation fails closed even
    if the sweep somehow missed the refresh row."""
    user_id = await _seed_user(integration_context, "usr_g_refresh")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_id, _access, refresh, _ = await _mint_grant_channel_tokens(
        integration_context, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    token_svc = TokenService(integration_context)
    access2, refresh2 = await token_svc.refresh(refresh, client_id=_CLIENT_ID)
    resolved = await token_svc.resolve_access_token(access2)
    assert resolved is not None and resolved.oauth_grant_id == grant_id

    # Simulate a sweep miss: revoke ONLY the grant row, then un-revoke the
    # rotated refresh token's revoked_at so the §4.5 re-check is the only
    # thing standing between a revoked grant and fresh tokens.
    async with integration_context.admin_db.session() as session:
        await OAuthClientGrantRepository.revoke(session, grant_id)
        await session.commit()
    async with integration_context.admin_db.session() as session:
        await session.execute(RefreshToken.__table__.update().values(revoked_at=None))
        await session.commit()

    with pytest.raises(InvalidGrantError, match="revoked"):
        await token_svc.refresh(refresh2, client_id=_CLIENT_ID)


async def test_revoke_grant_owner_admin_and_stranger(
    integration_context: Context, clean_grants: None
) -> None:
    """:revoke authorization: owner OK; admin OK; anyone else 403-mapped;
    unknown grant 404-mapped; repeat revoke is idempotent."""
    user_id = await _seed_user(integration_context, "usr_g_owner")
    stranger_id = await _seed_user(integration_context, "usr_g_stranger")
    admin_id = await _seed_user(integration_context, "usr_g_admin")
    agent_id = await _seed_agent(integration_context, owner_id=user_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)

    grant_id = await grant_svc.create_grant(
        user_id=user_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )

    with pytest.raises(OAuthGrantNotFoundError):
        await grant_svc.revoke_grant("ocg_missing", identity=Identity(sub=user_id, email=""))

    with pytest.raises(OAuthGrantAccessDeniedError):
        await grant_svc.revoke_grant(grant_id, identity=Identity(sub=stranger_id, email=""))
    grant = await grant_svc.get_grant(grant_id)
    assert grant is not None and grant.status == "active"

    # Owner revokes; a second revoke is an idempotent no-op.
    owner = Identity(sub=user_id, email="")
    await grant_svc.revoke_grant(grant_id, identity=owner)
    await grant_svc.revoke_grant(grant_id, identity=owner)
    grant = await grant_svc.get_grant(grant_id)
    assert grant is not None and grant.status == "revoked"

    # Admin can revoke a grant they do not own.
    grant_id_2 = await grant_svc.create_grant(
        user_id=user_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )
    await grant_svc.revoke_grant(
        grant_id_2, identity=Identity(sub=admin_id, email="", permissions=["org:admin"])
    )
    grant2 = await grant_svc.get_grant(grant_id_2)
    assert grant2 is not None and grant2.status == "revoked"
