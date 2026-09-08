"""Shared fixtures for the auth integration suite."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.context import Context
from tests.integration.auth.seeds import SEED_MARKER


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
            await session.execute(delete(User).where(User.created_by == SEED_MARKER))
            await session.commit()

    await _truncate()
    yield
    await _truncate()
