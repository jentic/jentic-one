"""Integration tests for the anonymous OAuth-client DCR flow (phase 3a-2).

Exercises OAuthDcrService and the 3a-1 approval verbs against a real database
(PostgreSQL or SQLite — no mocking): the §4.2 registration happy path
(pending + inactive rows, D9 auto-approve), the D8 dedupe key, and the §4.8
``oauth_client.registered`` / ``oauth_client.approved`` events with their
audit rows.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos.oauth_client_repo import redirect_uris_fingerprint
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.auth.services.errors import InvalidClientMetadataError
from jentic_one.auth.services.oauth_dcr_service import OAuthDcrService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventType
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus
from jentic_one.shared.scopes import MCP_TOOL_SCOPES

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_dcr_admin", email="dcr-admin@test.local")
_REDIRECT_URIS = ["http://localhost:33418/callback", "https://client.test.local/cb"]


@pytest.fixture()
def dcr_context(integration_context: Context) -> Generator[Context, None, None]:
    """The integration context with the DCR queue policy (no auto-approve).

    Restores the config afterwards — AppConfig is shared session state.
    """
    oauth_cfg = integration_context.config.server.mcp.oauth
    prior = oauth_cfg.auto_approve_clients
    oauth_cfg.auto_approve_clients = False
    yield integration_context
    oauth_cfg.auto_approve_clients = prior


@pytest.fixture()
async def clean_dcr_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    """Remove OAuth client rows plus their audit entries and lifecycle events."""

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            result = await session.execute(select(OAuthClient.id))
            ids = [row[0] for row in result.all()]
            if ids:
                await session.execute(delete(AuditEntry).where(AuditEntry.target_id.in_(ids)))
            await session.execute(
                delete(Event).where(
                    Event.type.in_(
                        [EventType.OAUTH_CLIENT_REGISTERED, EventType.OAUTH_CLIENT_APPROVED]
                    )
                )
            )
            await session.execute(delete(OAuthClient))

    await _clean()
    yield
    await _clean()


async def _events_of_type(ctx: Context, event_type: str) -> list[Event]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == event_type).order_by(Event.created_at.asc())
        )
        return list(result.scalars().all())


async def _row_by_client_id(ctx: Context, client_id: str) -> OAuthClient:
    async with ctx.admin_db.session() as session:
        return (
            await session.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        ).scalar_one()


async def test_register_lands_pending_inactive_public_agent_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """§4.2 happy path under the queue policy: pending + inactive, public-only,
    consent_model=agent, registration_source=dcr, RFC 7591-shaped result."""
    svc = OAuthDcrService(dcr_context)
    result = await svc.register(
        client_name="Cursor",
        redirect_uris=_REDIRECT_URIS,
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        scope="apis:read capabilities:execute org:admin",
        software_id="com.cursor.ide",
        software_version="1.2.3",
        application_type="native",
    )

    assert result.created is True
    assert result.client_id.startswith("oc_")
    assert result.client_id_issued_at > 0
    assert result.software_version == "1.2.3"
    assert result.application_type == "native"
    # org:admin is outside the MCP tool-scope cap and silently dropped.
    assert result.scope == "apis:read capabilities:execute"

    row = await _row_by_client_id(dcr_context, result.client_id)
    assert row.approval_status == "pending"
    assert row.active is False
    assert row.client_secret_hash is None
    assert row.token_endpoint_auth_method == "none"
    assert row.consent_model == "agent"
    assert row.registration_source == "dcr"
    assert row.software_id == "com.cursor.ide"
    assert row.allowed_scopes == ["apis:read", "capabilities:execute"]
    # The repo create path stamps the D8 dedupe fingerprint (§4.1).
    assert row.redirect_uris_fingerprint == redirect_uris_fingerprint(_REDIRECT_URIS)

    # Registration is durable: audit row (actor=dcr, origin=mcp)…
    async with dcr_context.admin_db.session() as session:
        audit = (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_type == AuditTargetType.OAUTH_CLIENT.value,
                    AuditEntry.target_id == row.id,
                    AuditEntry.action == AuditAction.REGISTER.value,
                )
            )
        ).scalar_one()
    assert audit.actor_type == "dcr"
    assert audit.origin == "mcp"

    # …plus an actionable oauth_client.registered event (§4.8).
    events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 1
    assert events[0].requires_action is True
    assert events[0].data["oauth_client_id"] == row.id
    assert events[0].data["client_id"] == result.client_id
    assert events[0].data["approval_status"] == "pending"


async def test_register_no_scope_claim_caps_to_mcp_tool_scopes(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """A DCR row is never unrestricted: no scope claim → the full MCP cap."""
    svc = OAuthDcrService(dcr_context)
    result = await svc.register(client_name="no-scope", redirect_uris=_REDIRECT_URIS)
    row = await _row_by_client_id(dcr_context, result.client_id)
    assert row.allowed_scopes == sorted(MCP_TOOL_SCOPES)


async def test_auto_approve_policy_activates_row_at_registration(
    integration_context: Context, clean_dcr_tables: None
) -> None:
    """D9 (OSS default): auto_approve_clients=true → approved + active row,
    non-actionable registered event, and the row passes /authorize validation."""
    assert integration_context.config.server.mcp.oauth.auto_approve_clients is True
    svc = OAuthDcrService(integration_context)
    result = await svc.register(
        client_name="Claude", redirect_uris=_REDIRECT_URIS, software_id="com.anthropic.claude"
    )

    row = await _row_by_client_id(integration_context, result.client_id)
    assert row.approval_status == "approved"
    assert row.active is True

    events = await _events_of_type(integration_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 1
    assert events[0].requires_action is False
    assert events[0].data["approval_status"] == "approved"

    client_svc = OAuthClientService(integration_context)
    assert await client_svc.is_redirect_uri_allowed(result.client_id, _REDIRECT_URIS[0]) is True
    assert await client_svc.is_public_client(result.client_id) is True


async def test_register_rejects_confidential_client_attempts(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """§4.2: this door only mints public clients; no row is written on reject."""
    svc = OAuthDcrService(dcr_context)
    with pytest.raises(InvalidClientMetadataError):
        await svc.register(
            client_name="confidential",
            redirect_uris=_REDIRECT_URIS,
            token_endpoint_auth_method="client_secret_basic",
        )
    async with dcr_context.admin_db.session() as session:
        assert (await session.execute(select(OAuthClient))).scalars().first() is None


async def test_dedupe_same_software_id_and_redirect_set_returns_existing(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """D8: exact (software_id + redirect set) match → the existing client_id,
    even with the redirect URIs reordered; only one row and one event exist."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    second = await svc.register(
        client_name="Cursor (renamed)",
        redirect_uris=list(reversed(_REDIRECT_URIS)),
        software_id="com.cursor.ide",
    )

    assert second.created is False
    assert second.client_id == first.client_id

    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1
    events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 1


async def test_dedupe_different_redirect_set_creates_new_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """D8: never dedupe on software_id alone — a differing redirect set is a
    new registration."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    second = await svc.register(
        client_name="Cursor",
        redirect_uris=["http://localhost:9999/other"],
        software_id="com.cursor.ide",
    )

    assert second.created is True
    assert second.client_id != first.client_id


async def test_no_software_id_never_dedupes(dcr_context: Context, clean_dcr_tables: None) -> None:
    """D8: without a software_id every register creates a fresh row."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="anon", redirect_uris=_REDIRECT_URIS)
    second = await svc.register(client_name="anon", redirect_uris=_REDIRECT_URIS)

    assert first.created is True and second.created is True
    assert second.client_id != first.client_id


async def test_approve_verb_emits_event_and_settles_registration_alert(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """§4.3/§4.8: :approve flips the row live, emits oauth_client.approved, and
    acknowledges the actionable registered alert."""
    dcr_svc = OAuthDcrService(dcr_context)
    result = await dcr_svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, result.client_id)

    client_svc = OAuthClientService(dcr_context)
    approved = await client_svc.approve(row.id, identity=_ADMIN)
    assert approved.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert approved.active is True

    approved_events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_APPROVED)
    assert len(approved_events) == 1
    assert approved_events[0].data["oauth_client_id"] == row.id
    assert approved_events[0].data["client_id"] == result.client_id
    assert approved_events[0].actor_id == _ADMIN.sub

    registered_events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(registered_events) == 1
    assert registered_events[0].acknowledged is True
    assert registered_events[0].acknowledged_by == _ADMIN.sub


async def test_deny_verb_settles_alert_without_approved_event(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    dcr_svc = OAuthDcrService(dcr_context)
    result = await dcr_svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, result.client_id)

    client_svc = OAuthClientService(dcr_context)
    denied = await client_svc.deny(row.id, reason="not vetted", identity=_ADMIN)
    assert denied.approval_status == OAuthClientApprovalStatus.DENIED.value

    assert await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_APPROVED) == []
    registered_events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert registered_events[0].acknowledged is True
