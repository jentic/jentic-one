"""Integration tests for the anonymous OAuth-client DCR flow.

Exercises OAuthDcrService and the admin approval verbs against a real database
(PostgreSQL or SQLite — no mocking): the registration happy path
(pending + inactive rows, auto-approve), the dedupe key, and the
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
from jentic_one.admin.repos.oauth_client_repo import (
    OAuthClientRepository,
    redirect_uris_fingerprint,
)
from jentic_one.admin.services._support.tokens import generate_client_id
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
    """The integration context pinned to the DCR queue policy (no auto-approve).

    This is the default posture (D9 as amended), pinned explicitly so the
    tests document what they exercise. Restores the config afterwards —
    AppConfig is shared session state.
    """
    oauth_cfg = integration_context.config.server.mcp.oauth
    prior = oauth_cfg.auto_approve_clients
    oauth_cfg.auto_approve_clients = False
    yield integration_context
    oauth_cfg.auto_approve_clients = prior


@pytest.fixture()
def auto_approve_context(integration_context: Context) -> Generator[Context, None, None]:
    """The integration context with the explicit auto-approve opt-in (D9).

    Restores the config afterwards — AppConfig is shared session state.
    """
    oauth_cfg = integration_context.config.server.mcp.oauth
    prior = oauth_cfg.auto_approve_clients
    oauth_cfg.auto_approve_clients = True
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
    """Happy path under the queue policy: pending + inactive, public-only,
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
    # The repo create path stamps the dedupe fingerprint.
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

    # …plus an actionable oauth_client.registered event.
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


async def test_register_zero_overlap_scope_rejected_no_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """A scope claim with no MCP-tool-scope overlap is rejected outright: an
    empty ceiling ``[]`` would collapse to the ``None`` "no allowlist"
    sentinel in the admin view and skip the /authorize scope check entirely —
    an *unrestricted* client, which this door never mints. No row is written."""
    svc = OAuthDcrService(dcr_context)
    with pytest.raises(InvalidClientMetadataError, match="no overlap"):
        await svc.register(
            client_name="privileged-only",
            redirect_uris=_REDIRECT_URIS,
            scope="org:admin agents:write",
        )
    async with dcr_context.admin_db.session() as session:
        assert (await session.execute(select(OAuthClient))).scalars().first() is None


async def test_auto_approve_policy_activates_row_at_registration(
    auto_approve_context: Context, clean_dcr_tables: None
) -> None:
    """D9 (explicit opt-in): auto_approve_clients=true → approved + active row,
    non-actionable registered event, and the row passes /authorize validation."""
    assert auto_approve_context.config.server.mcp.oauth.auto_approve_clients is True
    svc = OAuthDcrService(auto_approve_context)
    result = await svc.register(
        client_name="Claude", redirect_uris=_REDIRECT_URIS, software_id="com.anthropic.claude"
    )

    row = await _row_by_client_id(auto_approve_context, result.client_id)
    assert row.approval_status == "approved"
    assert row.active is True

    events = await _events_of_type(auto_approve_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 1
    assert events[0].requires_action is False
    assert events[0].data["approval_status"] == "approved"

    client_svc = OAuthClientService(auto_approve_context)
    assert await client_svc.is_redirect_uri_allowed(result.client_id, _REDIRECT_URIS[0]) is True
    assert await client_svc.is_public_client(result.client_id) is True


async def test_register_rejects_confidential_client_attempts(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """This door only mints public clients; no row is written on reject."""
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
    """Dedupe: exact (software_id + redirect set) match → the existing client_id,
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
    """Never dedupe on software_id alone — a differing redirect set is a
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


async def test_no_software_id_same_name_and_redirect_set_dedupes(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """G13 (#1251): a software_id-less client (Cursor, mcp-remote) retrying
    registration re-attaches to its existing row via the (client_name +
    redirect set) fallback key — the awaiting-approval retry loop must not
    mint a fresh pending row (34 duplicate 'Cursor' rows observed live in
    one morning). Redirect-URI order must not matter."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    second = await svc.register(client_name="Cursor", redirect_uris=list(reversed(_REDIRECT_URIS)))

    assert first.created is True
    assert second.created is False
    assert second.client_id == first.client_id

    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1
    # No second actionable alert for the dedupe hit — the queue stays clean.
    events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 1


async def test_no_software_id_different_name_creates_new_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """The fallback key is (name + redirect set), never the redirect set
    alone: two distinct software_id-less clients sharing redirect URIs
    (e.g. the same well-known loopback port) stay distinct rows."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    second = await svc.register(client_name="MCP CLI Proxy", redirect_uris=_REDIRECT_URIS)

    assert second.created is True
    assert second.client_id != first.client_id


async def test_no_software_id_different_redirect_set_creates_new_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Never dedupe on client_name alone — a differing redirect set is a new
    registration (the fingerprint guarantees an approved row's redirect_uris
    are never silently widened by a re-register)."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    second = await svc.register(client_name="Cursor", redirect_uris=["http://localhost:9999/other"])

    assert second.created is True
    assert second.client_id != first.client_id


async def test_no_software_id_dedupe_preserves_row_state(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """A fallback-key dedupe hit is read-only: the stored row keeps its
    pending status, inactive flag, name, and redirect set verbatim — a
    re-registration never resets the approval lifecycle."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    second = await svc.register(
        client_name="Cursor",
        redirect_uris=list(reversed(_REDIRECT_URIS)),
        scope="apis:read",
    )

    assert second.created is False
    row = await _row_by_client_id(dcr_context, first.client_id)
    assert row.approval_status == OAuthClientApprovalStatus.PENDING.value
    assert row.active is False
    assert row.name == "Cursor"
    assert row.redirect_uris == _REDIRECT_URIS
    assert row.allowed_scopes == sorted(MCP_TOOL_SCOPES)


async def test_no_software_id_reattach_to_approved_row_keeps_it_approved(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Continuity across a client cache loss: a re-register against an
    *approved* software_id-less row returns the same client_id, still
    approved and active — no fresh approval, no fresh consent. Adopting the
    row discloses only the (public, RFC 6749 §2.2) client_id: public clients
    hold no secret (PKCE per flow) and consent stays per-grant."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    row = await _row_by_client_id(dcr_context, first.client_id)
    await OAuthClientService(dcr_context).approve(row.id, identity=_ADMIN)

    second = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)

    assert second.created is False
    assert second.client_id == first.client_id
    refreshed = await _row_by_client_id(dcr_context, first.client_id)
    assert refreshed.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert refreshed.active is True


async def test_dedupe_key_spaces_never_cross_match(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Adversarial guard: the software_id key space and the fallback name key
    space are disjoint. A software_id-less registration replaying an existing
    row's name + redirect set must not adopt a row registered *with* a
    software_id, and a software_id-bearing registration must not adopt a
    software_id-less row of the same name + redirect set."""
    svc = OAuthDcrService(dcr_context)
    with_sid = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    without_sid = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    assert without_sid.created is True
    assert without_sid.client_id != with_sid.client_id

    other_sid = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.evil.other"
    )
    assert other_sid.created is True
    assert other_sid.client_id not in {with_sid.client_id, without_sid.client_id}


async def test_approve_verb_emits_event_and_settles_registration_alert(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """:approve flips the row live, emits oauth_client.approved, and
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


async def test_dedupe_denied_row_returns_same_client_id_and_stays_denied(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Denied arm of the dedupe: an exact re-registration against a *denied* row
    returns the same client_id (200-shaped, created=False) and does not mint
    a fresh pending row or a second chance — recovery is admin-actioned only."""
    dcr_svc = OAuthDcrService(dcr_context)
    first = await dcr_svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, first.client_id)
    await OAuthClientService(dcr_context).deny(row.id, reason="not vetted", identity=_ADMIN)

    second = await dcr_svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    assert second.created is False
    assert second.client_id == first.client_id
    refreshed = await _row_by_client_id(dcr_context, first.client_id)
    assert refreshed.approval_status == OAuthClientApprovalStatus.DENIED.value
    assert refreshed.active is False
    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1
    # No second registered event for the dedupe hit.
    assert len(await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)) == 1


async def test_denied_then_approved_recovery_emits_approved_event(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Denied → approved recovery: deny is reversible; a later :approve
    re-arms the row and fires oauth_client.approved (the events.py "including
    re-approval of a previously denied client" arm)."""
    dcr_svc = OAuthDcrService(dcr_context)
    result = await dcr_svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, result.client_id)

    client_svc = OAuthClientService(dcr_context)
    await client_svc.deny(row.id, reason="not vetted", identity=_ADMIN)
    recovered = await client_svc.approve(row.id, identity=_ADMIN)

    assert recovered.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert recovered.active is True
    approved_events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_APPROVED)
    assert len(approved_events) == 1
    assert approved_events[0].data["oauth_client_id"] == row.id
    # The cached client_id is usable again after recovery.
    second = await dcr_svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    assert second.created is False
    assert second.client_id == result.client_id


async def test_dedupe_prefers_approved_row_over_older_unapproved(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """F6: when a double-register race left multiple rows for one dedupe key
    and the admin approved the *newer* one, re-registers must surface the
    approved row — not keep returning the older pending/denied client_id."""
    async with dcr_context.admin_db.transaction() as session:
        older = await OAuthClientRepository.create(
            session,
            client_id=generate_client_id(),
            name="Cursor (lost race)",
            redirect_uris=_REDIRECT_URIS,
            client_secret_hash=None,
            allowed_scopes=sorted(MCP_TOOL_SCOPES),
            token_endpoint_auth_method="none",
            consent_model="agent",
            registration_source="dcr",
            software_id="com.cursor.ide",
            approval_status=OAuthClientApprovalStatus.PENDING.value,
            active=False,
            created_by="dcr",
        )
        newer = await OAuthClientRepository.create(
            session,
            client_id=generate_client_id(),
            name="Cursor (approved)",
            redirect_uris=_REDIRECT_URIS,
            client_secret_hash=None,
            allowed_scopes=sorted(MCP_TOOL_SCOPES),
            token_endpoint_auth_method="none",
            consent_model="agent",
            registration_source="dcr",
            software_id="com.cursor.ide",
            approval_status=OAuthClientApprovalStatus.APPROVED.value,
            active=True,
            created_by="dcr",
        )
        older_client_id, newer_client_id = older.client_id, newer.client_id

    result = await OAuthDcrService(dcr_context).register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    assert result.created is False
    assert result.client_id == newer_client_id
    assert result.client_id != older_client_id


async def test_dedupe_response_echoes_request_metadata_not_stored_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """F5 minimization: a dedupe 200 reflects the *request's* validated
    metadata — admin-side edits to the stored row (rename, scope changes)
    never leak to an anonymous re-registrant. Only client_id (+ issued_at)
    comes from the row."""
    dcr_svc = OAuthDcrService(dcr_context)
    first = await dcr_svc.register(
        client_name="Cursor",
        redirect_uris=_REDIRECT_URIS,
        scope="apis:read capabilities:execute",
        software_id="com.cursor.ide",
    )
    row = await _row_by_client_id(dcr_context, first.client_id)
    await OAuthClientService(dcr_context).update(
        row.id, name="Admin Renamed", allowed_scopes=["apis:read"], identity=_ADMIN
    )

    second = await dcr_svc.register(
        client_name="Cursor v2",
        redirect_uris=_REDIRECT_URIS,
        grant_types=["authorization_code"],
        scope="capabilities:execute",
        software_id="com.cursor.ide",
        software_version="2.0.0",
        application_type="native",
    )

    assert second.created is False
    assert second.client_id == first.client_id
    # Echoes of the caller's own submission — not the admin-edited row state.
    assert second.client_name == "Cursor v2"
    assert second.scope == "capabilities:execute"
    assert second.grant_types == ["authorization_code"]
    assert second.software_version == "2.0.0"
    assert second.application_type == "native"
    # The stored row keeps its admin-edited state.
    refreshed = await _row_by_client_id(dcr_context, first.client_id)
    assert refreshed.name == "Admin Renamed"
    assert refreshed.allowed_scopes == ["apis:read"]
