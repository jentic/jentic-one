"""Shared seed helpers for the grant-channel integration tests.

Used by ``test_oauth_grant_channel.py`` (service-level) and
``test_oauth_consent_web.py`` (web-level). All rows are stamped
``created_by=SEED_MARKER`` so the shared ``clean_grants`` fixture (in this
package's ``conftest.py``) can remove them without touching unrelated state.
"""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode

from jentic_one.admin.repos import (
    ActorScopeGrantRepository,
    AgentRepository,
    OAuthClientRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType

SEED_MARKER = "usr_grant_seed"
CLIENT_ID = "oc_grant_channel_test"
REDIRECT_URI = "https://mcpapp.example.com/cb"
CODE_VERIFIER = "grant-channel-verifier-0123456789abcdef0123456789abcdef"


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode()


async def seed_user(ctx: Context, user_id: str) -> str:
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            id=user_id,
            email=f"{user_id}@grants.test",
            first_name="Grant",
            last_name="Test",
            active=True,
            created_by=SEED_MARKER,
        )
        await session.commit()
        return user.id


#: Password used by every seeded local-login account (see seed_password_user).
SEED_PASSWORD = "correct horse battery staple"


async def seed_password_user(ctx: Context, user_id: str) -> tuple[str, str]:
    """Seed a user with a password (SEED_PASSWORD); returns (user_id, email).

    Shared by the local-login and session-continue web suites — both walk the
    /authorize flow against a first-party password account.
    """
    uid = await seed_user(ctx, user_id)
    async with ctx.admin_db.session() as session:
        await UserSecretRepository.set_password_hash(
            session, uid, password_hash=hash_password(SEED_PASSWORD), created_by=SEED_MARKER
        )
        await session.commit()
    return uid, f"{uid}@grants.test"


async def seed_agent(
    ctx: Context,
    *,
    owner_id: str,
    scopes: list[str],
    status: ActorStatus = ActorStatus.ACTIVE,
    name: str = "grant-test-agent",
) -> str:
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name=name,
            owner_id=owner_id,
            registered_by=owner_id,
            created_by=SEED_MARKER,
            status=status,
        )
        for scope in scopes:
            await ActorScopeGrantRepository.grant(
                session,
                actor_id=agent.id,
                actor_type=ActorType.AGENT,
                scope=scope,
                granted_by=owner_id,
                created_by=SEED_MARKER,
            )
        await session.commit()
        return agent.id


async def seed_permissions(ctx: Context, user_id: str, permissions: list[str]) -> None:
    """Grant assigned permissions to a seeded user (P4 hybrid arm control).

    The inline consent creation splits on the user's effective permissions
    (``agents:write`` → ACTIVE, else PENDING), so web tests that expect the
    original ACTIVE arm must hold the grant the SPA door would demand.
    Stamped ``created_by=SEED_MARKER`` for the shared cleanup.
    """
    async with ctx.admin_db.session() as session:
        await UserPermissionGrantRepository.set_permissions(
            session,
            user_id,
            permissions=set(permissions),
            granted_by=None,
            created_by=SEED_MARKER,
        )
        await session.commit()


async def seed_client(
    ctx: Context,
    *,
    allowed_scopes: list[str] | None,
    client_id: str = CLIENT_ID,
) -> str:
    async with ctx.admin_db.session() as session:
        client = await OAuthClientRepository.create(
            session,
            client_id=client_id,
            name="Grant Channel App",
            redirect_uris=[REDIRECT_URI],
            client_secret_hash=None,
            allowed_scopes=allowed_scopes,
            token_endpoint_auth_method="none",
            consent_model="agent",
            registration_source="dcr",
            created_by=SEED_MARKER,
        )
        await session.commit()
        return client.client_id


async def mint_grant_channel_tokens(
    ctx: Context,
    *,
    user_id: str,
    agent_id: str,
    grant_scopes: list[str],
    client_id: str = CLIENT_ID,
) -> tuple[str, str, str, str | None]:
    """Consent-approve + code issue + exchange. Returns (grant_id, at, rt, id_token).

    Shared by the grant-channel and ownership-transfer suites so both mint
    through the real consent→code→exchange path rather than seeding token
    rows by hand.
    """
    grant_svc = OAuthGrantService(ctx)
    authorize_svc = AuthorizeService(ctx)
    grant_id = await grant_svc.create_grant(
        user_id=user_id,
        oauth_client_id=client_id,
        agent_id=agent_id,
        scopes=grant_scopes,
        client_name="Grant Channel App",
    )
    code = await authorize_svc.issue_authorization_code(
        user_id=user_id,
        client_id=client_id,
        redirect_uri=REDIRECT_URI,
        code_challenge=code_challenge(CODE_VERIFIER),
        scopes=" ".join(grant_scopes),
        grant_id=grant_id,
    )
    access, refresh, id_token, _scopes = await authorize_svc.exchange_code(
        code=code,
        code_verifier=CODE_VERIFIER,
        redirect_uri=REDIRECT_URI,
        client_id=client_id,
        oauth_client_id=client_id,
    )
    return grant_id, access, refresh, id_token
