"""Shared fixtures for the auth integration suite."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from sqlalchemy import delete

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.config import PlatformClientConfig, SigningKeyConfig
from jentic_one.shared.context import Context
from tests.integration.auth.seeds import SEED_MARKER

#: Platform client registered by the ``local_login_ctx`` fixture — shared by
#: the local-login and session-continue web suites.
LOCAL_LOGIN_PLATFORM_CLIENT_ID = "local-login-platform"
LOCAL_LOGIN_PLATFORM_REDIRECT = "https://platform.test.local/cb"


@pytest.fixture()
async def clean_grants(integration_context: Context) -> AsyncGenerator[None, None]:
    """Empty the grant-channel tables before and after each test.

    Token/code/grant/scope-grant/external-identity tables are cleared wholesale
    (tests own them per-run); users/agents/clients only where seeded through
    :mod:`tests.integration.auth.seeds` (``created_by == SEED_MARKER``).
    """

    async def _truncate() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AccessToken))
            await session.execute(delete(RefreshToken))
            await session.execute(delete(AuthorizationCode))
            await session.execute(delete(OAuthClientGrant))
            await session.execute(delete(ActorScopeGrant))
            await session.execute(delete(ExternalIdentity))
            await session.execute(delete(OAuthClient).where(OAuthClient.created_by == SEED_MARKER))
            await session.execute(delete(Agent).where(Agent.created_by == SEED_MARKER))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.created_by == SEED_MARKER)
            )
            await session.execute(delete(User).where(User.created_by == SEED_MARKER))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


@pytest.fixture()
def local_login_ctx(integration_context: Context) -> Generator[Context, None, None]:
    """Integration context with the local-login gate ON and the IdP OFF.

    Promoted from the local-login suite (#1276) — the session-continue suite
    (#1299) walks the same deployment shape. Only config is mutated (gate on,
    IdP off, an ephemeral signing key, one extra platform client), and it is
    restored — AppConfig is shared session state.
    """
    auth_cfg = integration_context.config.auth
    prior_enabled = auth_cfg.local_login.enabled
    prior_idp_enabled = auth_cfg.idp.enabled
    prior_signing = auth_cfg.id_signing
    auth_cfg.local_login.enabled = True
    auth_cfg.idp.enabled = False
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode()
    auth_cfg.id_signing = [
        SigningKeyConfig(kid="local-login-test-key", private_key_pem=pem)  # type: ignore[arg-type]
    ]
    auth_cfg.platform_clients.append(
        PlatformClientConfig(
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uris=[LOCAL_LOGIN_PLATFORM_REDIRECT],
        )
    )
    yield integration_context
    auth_cfg.local_login.enabled = prior_enabled
    auth_cfg.idp.enabled = prior_idp_enabled
    auth_cfg.id_signing = prior_signing
    auth_cfg.platform_clients = [
        pc for pc in auth_cfg.platform_clients if pc.client_id != LOCAL_LOGIN_PLATFORM_CLIENT_ID
    ]


@pytest.fixture()
async def clean_user_secrets(integration_context: Context) -> AsyncGenerator[None, None]:
    """Remove password rows seeded by this suite (clean_grants only covers users)."""

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(UserSecret).where(UserSecret.created_by == SEED_MARKER))

    await _clean()
    yield
    await _clean()
