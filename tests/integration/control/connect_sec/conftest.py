"""Clean-slate fixture for the connect-session security regression suite."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, text, update

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.control.core.schema.connect_session_outcomes import ConnectSessionOutcome
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.shared.config import DirectOAuth2ProviderConfig
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    FOREIGN_AGENT_ID,
    OTHER_OWNER_ID,
    OWNER_ID,
    SIBLING_AGENT_ID,
)


async def _wipe(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        for table in (
            OperationURLIndex,
            SecuritySchemeFlow,
            SecurityScheme,
            ServerVariable,
            Server,
            Operation,
            SpecFile,
        ):
            await session.execute(delete(table))
        await session.execute(update(Api).values(current_revision_id=None))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.commit()
    async with ctx.control_db.session() as session:
        for control_table in (
            ConnectSessionOutcome,
            ConnectSession,
            OAuthToken,
            OAuthClientCredential,
            Credential,
            OAuthAppRegistration,
        ):
            await session.execute(delete(control_table))
        await session.execute(text("DELETE FROM agent_permission_rules"))
        await session.commit()
    async with ctx.admin_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_sec_%'")
        )
        await session.execute(text("DELETE FROM agents WHERE id LIKE 'agnt_sec_%'"))
        await session.execute(text("DELETE FROM users WHERE id LIKE 'usr_sec_%'"))
        await session.execute(delete(AuditEntry))
        await session.execute(delete(Event))
        await session.commit()


@pytest.fixture()
async def env(
    integration_context: Context, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[Context, None]:
    """Manual flows on, a clean slate, two owners, the owner's two agents and a foreign one."""
    ctx = integration_context
    monkeypatch.setattr(ctx.config.control.connect, "manual_flows_enabled", True)
    monkeypatch.setattr(ctx.config.vendors, "entries", {})
    ctx.config.credentials.providers.setdefault(
        "direct_oauth2",
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback"
        ),
    )
    await _wipe(ctx)
    async with ctx.admin_db.session() as session:
        for user_id in (OWNER_ID, OTHER_OWNER_ID):
            await session.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name)"
                    " VALUES (:id, :email, 'S', 'C')"
                ),
                {"id": user_id, "email": f"{user_id}@example.test"},
            )
        for agent_id, owner in (
            (AGENT_ID, OWNER_ID),
            (SIBLING_AGENT_ID, OWNER_ID),
            (FOREIGN_AGENT_ID, OTHER_OWNER_ID),
        ):
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, owner_id, status)"
                    " VALUES (:id, :id, :owner, :owner, 'approved')"
                ),
                {"id": agent_id, "owner": owner},
            )
        await session.commit()
    yield ctx
    await _wipe(ctx)
