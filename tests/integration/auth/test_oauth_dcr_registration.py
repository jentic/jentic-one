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


async def test_empty_string_software_id_normalizes_to_null_key_space(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """F1 regression: ``software_id: ""`` passes schema validation and, before
    normalization, took the fallback lookup (``IS NULL``) while *storing*
    ``""`` (NOT NULL) — a row in neither key space that re-opened the #1251
    loop. Falsy/whitespace software_id must normalize to NULL at the service
    boundary, so all its spellings land in the fallback key space and dedupe
    with each other."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="")
    row = await _row_by_client_id(dcr_context, first.client_id)
    assert row.software_id is None
    assert first.software_id is None  # The response echoes the normalized value.

    # Two ""-registrations dedupe to one row…
    second = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="")
    assert second.created is False
    assert second.client_id == first.client_id

    # …and "", whitespace-only, and absent software_id share one key space.
    for spelling in (None, "   "):
        again = await svc.register(
            client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id=spelling
        )
        assert again.created is False, f"software_id={spelling!r} minted a duplicate row"
        assert again.client_id == first.client_id

    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1


async def test_dedupe_hit_writes_audit_row_but_no_event(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """F2: every dedupe re-attach leaves a forensic audit trace (the 200 arm
    discloses an existing client_id to an anonymous caller), while the event
    stream stays quiet — no fresh approval-queue alert per retry."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    second = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    assert second.created is False

    row = await _row_by_client_id(dcr_context, first.client_id)
    async with dcr_context.admin_db.session() as session:
        audits = (
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_type == AuditTargetType.OAUTH_CLIENT.value,
                        AuditEntry.target_id == row.id,
                        AuditEntry.action == AuditAction.REGISTER.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    by_reason = {audit.reason: audit for audit in audits}
    assert set(by_reason) == {
        "anonymous dynamic client registration",
        "anonymous DCR re-attach (dedupe hit)",
    }
    reattach = by_reason["anonymous DCR re-attach (dedupe hit)"]
    assert reattach.actor_type == "dcr"
    assert reattach.origin == "mcp"
    assert reattach.after is not None
    assert reattach.after["client_id"] == first.client_id
    assert reattach.after["approval_status"] == OAuthClientApprovalStatus.PENDING.value
    # The event stream stays quiet: one registered event, nothing per retry.
    events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 1


async def test_no_software_id_dedupe_denied_row_stays_denied(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """F6(b) — denied arm of the *fallback* key (the existing denied test is
    software_id-only): a software_id-less re-registration against a denied
    row re-attaches (created=False, same client_id) and never mints a fresh
    pending second chance; recovery stays admin-actioned."""
    svc = OAuthDcrService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    row = await _row_by_client_id(dcr_context, first.client_id)
    await OAuthClientService(dcr_context).deny(row.id, reason="not vetted", identity=_ADMIN)

    second = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)

    assert second.created is False
    assert second.client_id == first.client_id
    refreshed = await _row_by_client_id(dcr_context, first.client_id)
    assert refreshed.approval_status == OAuthClientApprovalStatus.DENIED.value
    assert refreshed.active is False
    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1


async def test_admin_created_row_never_adopted_by_anonymous_door(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """F6(c) — adversarial pin on the ``registration_source='dcr'`` filter:
    an admin-created client with the same name + redirect set (and no
    software_id) must never be adopted by an anonymous registration — that
    filter is the only line between the anonymous door and admin rows."""
    async with dcr_context.admin_db.transaction() as session:
        admin_row = await OAuthClientRepository.create(
            session,
            client_id=generate_client_id(),
            name="Cursor",
            redirect_uris=_REDIRECT_URIS,
            client_secret_hash=None,
            allowed_scopes=sorted(MCP_TOOL_SCOPES),
            token_endpoint_auth_method="none",
            consent_model="agent",
            registration_source="admin",
            software_id=None,
            approval_status=OAuthClientApprovalStatus.APPROVED.value,
            active=True,
            created_by=_ADMIN.sub,
        )
        admin_client_id = admin_row.client_id

    result = await OAuthDcrService(dcr_context).register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS
    )

    assert result.created is True
    assert result.client_id != admin_client_id
    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 2


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


async def _create_dcr_row(
    ctx: Context,
    *,
    name: str,
    approval_status: str,
    active: bool,
    software_id: str | None = "com.cursor.ide",
) -> str:
    """Insert a raw DCR row (race artifact) and return its public client_id."""
    async with ctx.admin_db.transaction() as session:
        row = await OAuthClientRepository.create(
            session,
            client_id=generate_client_id(),
            name=name,
            redirect_uris=_REDIRECT_URIS,
            client_secret_hash=None,
            allowed_scopes=sorted(MCP_TOOL_SCOPES),
            token_endpoint_auth_method="none",
            consent_model="agent",
            registration_source="dcr",
            software_id=software_id,
            approval_status=approval_status,
            active=active,
            created_by="dcr",
        )
        return row.client_id


async def test_reattach_to_deactivated_approved_row_requeues_as_pending(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """#1312: a kill-switched row (admin soft-delete → approved + inactive)
    fails the D7 gate at every enforcement door but was re-attached by the
    dedupe as-is — announcing "approved" for a client every other door
    refuses, in a state visible in no admin UI tab. A re-register must
    instead re-enter the approval queue: the row flips to pending (active
    stays false, D7 pending construction), lands in the pending tab, and the
    flip is audited and alerted."""
    svc = OAuthDcrService(dcr_context)
    client_svc = OAuthClientService(dcr_context)
    first = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, first.client_id)
    await client_svc.approve(row.id, identity=_ADMIN)
    # The UI Delete path: DELETE /admin/oauth-clients/{id} → soft-delete.
    await client_svc.deactivate(row.id, identity=_ADMIN)

    second = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    # Same identity, 200-shaped — no fresh row, no queue spam.
    assert second.created is False
    assert second.client_id == first.client_id
    refreshed = await _row_by_client_id(dcr_context, first.client_id)
    assert refreshed.approval_status == OAuthClientApprovalStatus.PENDING.value
    assert refreshed.active is False
    async with dcr_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1

    # The row is admin-visible again: the pending tab lists it.
    pending_views = await client_svc.list_all(
        approval_status=OAuthClientApprovalStatus.PENDING.value
    )
    assert [v.client_id for v in pending_views] == [first.client_id]

    # The flip is a status transition by the anonymous DCR actor → audited
    # with before/after and a reason (F2 precedent).
    async with dcr_context.admin_db.session() as session:
        flip_audits = (
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_type == AuditTargetType.OAUTH_CLIENT.value,
                        AuditEntry.target_id == row.id,
                        AuditEntry.action == AuditAction.UPDATE.value,
                        AuditEntry.actor_type == "dcr",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(flip_audits) == 1
    flip = flip_audits[0]
    assert flip.reason is not None and "#1312" in flip.reason
    assert flip.before == {"approval_status": "approved", "active": False}
    assert flip.after == {"approval_status": "pending", "active": False}
    assert flip.origin == "mcp"

    # The flip re-arms the approval-queue alert (the original registered
    # event was settled by :approve).
    events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert len(events) == 2
    requeue_event = events[-1]
    assert requeue_event.requires_action is True
    assert requeue_event.data["oauth_client_id"] == row.id
    assert requeue_event.data["approval_status"] == OAuthClientApprovalStatus.PENDING.value

    # G13 survives: further retries of the now-pending client are quiet
    # re-attaches — no second flip audit, no third event.
    third = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    assert third.created is False
    assert third.client_id == first.client_id
    assert len(await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)) == 2
    async with dcr_context.admin_db.session() as session:
        flip_count = len(
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_id == row.id,
                        AuditEntry.action == AuditAction.UPDATE.value,
                        AuditEntry.actor_type == "dcr",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert flip_count == 1


async def test_requeued_client_needs_explicit_admin_reapproval(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """#1312: after the re-queue, the client is NOT usable until an admin
    explicitly re-approves — silent resurrection to approved is impossible.
    A fresh :approve settles the re-queue alert and re-arms the row (D7)."""
    svc = OAuthDcrService(dcr_context)
    client_svc = OAuthClientService(dcr_context)
    first = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, first.client_id)
    await client_svc.approve(row.id, identity=_ADMIN)
    await client_svc.deactivate(row.id, identity=_ADMIN)

    await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    # Re-queued, not resurrected: every OAuth entry point still refuses it.
    assert await client_svc.is_public_client(first.client_id) is False
    assert await client_svc.is_redirect_uri_allowed(first.client_id, _REDIRECT_URIS[0]) is False

    recovered = await client_svc.approve(row.id, identity=_ADMIN)
    assert recovered.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert recovered.active is True
    assert await client_svc.is_public_client(first.client_id) is True
    # The re-queue alert is settled by the decision.
    events = await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED)
    assert all(e.acknowledged for e in events if e.requires_action)


async def test_dedupe_prefers_active_approved_over_deactivated_approved(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Race arm (#1312): among exact matches the winner is the row the
    client can actually *use* (the D7 gate: active AND approved) — not
    merely the oldest approved row. A deactivated approved sibling must
    neither win nor be mutated."""
    zombie_id = await _create_dcr_row(
        dcr_context, name="Cursor (killed)", approval_status="approved", active=False
    )
    usable_id = await _create_dcr_row(
        dcr_context, name="Cursor (usable)", approval_status="approved", active=True
    )

    result = await OAuthDcrService(dcr_context).register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    assert result.created is False
    assert result.client_id == usable_id
    # The zombie sibling is untouched — no flip, no event.
    zombie = await _row_by_client_id(dcr_context, zombie_id)
    assert zombie.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert zombie.active is False
    assert await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED) == []


async def test_dedupe_prefers_pending_over_deactivated_approved(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Race arm (#1312): an honestly-pending row outranks a kill-switched
    approved row — the client re-attaches to the queue entry that already
    tells the true story, and nothing is mutated."""
    zombie_id = await _create_dcr_row(
        dcr_context, name="Cursor (killed)", approval_status="approved", active=False
    )
    pending_id = await _create_dcr_row(
        dcr_context, name="Cursor (pending)", approval_status="pending", active=False
    )

    result = await OAuthDcrService(dcr_context).register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    assert result.created is False
    assert result.client_id == pending_id
    zombie = await _row_by_client_id(dcr_context, zombie_id)
    assert zombie.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert zombie.active is False


async def test_dedupe_prefers_denied_over_deactivated_approved(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """Race arm (#1312): a deny is a visible, admin-reversible verdict and
    must never be sidestepped by adopting (and re-queueing) a deactivated
    approved sibling — the denied row wins and stays denied."""
    zombie_id = await _create_dcr_row(
        dcr_context, name="Cursor (killed)", approval_status="approved", active=False
    )
    denied_id = await _create_dcr_row(
        dcr_context, name="Cursor (denied)", approval_status="denied", active=False
    )

    result = await OAuthDcrService(dcr_context).register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )

    assert result.created is False
    assert result.client_id == denied_id
    denied = await _row_by_client_id(dcr_context, denied_id)
    assert denied.approval_status == OAuthClientApprovalStatus.DENIED.value
    zombie = await _row_by_client_id(dcr_context, zombie_id)
    assert zombie.approval_status == OAuthClientApprovalStatus.APPROVED.value
    assert zombie.active is False
    # No pending row was manufactured around the deny.
    assert await _events_of_type(dcr_context, EventType.OAUTH_CLIENT_REGISTERED) == []


async def test_reattach_requeues_in_fallback_key_space_too(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """#1312, G13 fallback arm: the re-queue is key-space agnostic — a
    software_id-less client (Cursor, mcp-remote) whose approved row was
    deactivated also re-enters the approval queue on re-register."""
    svc = OAuthDcrService(dcr_context)
    client_svc = OAuthClientService(dcr_context)
    first = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)
    row = await _row_by_client_id(dcr_context, first.client_id)
    await client_svc.approve(row.id, identity=_ADMIN)
    await client_svc.deactivate(row.id, identity=_ADMIN)

    second = await svc.register(client_name="Cursor", redirect_uris=_REDIRECT_URIS)

    assert second.created is False
    assert second.client_id == first.client_id
    refreshed = await _row_by_client_id(dcr_context, first.client_id)
    assert refreshed.approval_status == OAuthClientApprovalStatus.PENDING.value
    assert refreshed.active is False


async def test_auto_approve_policy_does_not_resurrect_killswitched_row(
    auto_approve_context: Context, clean_dcr_tables: None
) -> None:
    """#1312 vs D9: the blanket auto-approve policy applies to *new* rows
    only — a re-register on a row an admin explicitly deactivated re-queues
    it as pending (the dedupe wins over the create arm), never silently
    re-approves it. The admin kill switch beats the deployment policy."""
    svc = OAuthDcrService(auto_approve_context)
    client_svc = OAuthClientService(auto_approve_context)
    first = await svc.register(
        client_name="Claude", redirect_uris=_REDIRECT_URIS, software_id="com.anthropic.claude"
    )
    row = await _row_by_client_id(auto_approve_context, first.client_id)
    assert row.approval_status == OAuthClientApprovalStatus.APPROVED.value  # D9 auto-approved
    await client_svc.deactivate(row.id, identity=_ADMIN)

    second = await svc.register(
        client_name="Claude", redirect_uris=_REDIRECT_URIS, software_id="com.anthropic.claude"
    )

    assert second.created is False
    assert second.client_id == first.client_id
    refreshed = await _row_by_client_id(auto_approve_context, first.client_id)
    assert refreshed.approval_status == OAuthClientApprovalStatus.PENDING.value
    assert refreshed.active is False
    assert await client_svc.is_public_client(first.client_id) is False


async def test_requeue_cas_guard_never_downgrades_a_live_row(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """#1312 race pin: the re-queue is a compare-and-set — against a row
    that is (or has concurrently become) approved+active, the guarded
    UPDATE writes nothing and reports False, so a re-register racing an
    admin ``:approve`` can never knock the just-approved client back to
    pending. Against a genuinely kill-switched row it flips exactly once."""
    svc = OAuthDcrService(dcr_context)
    client_svc = OAuthClientService(dcr_context)
    first = await svc.register(
        client_name="Cursor", redirect_uris=_REDIRECT_URIS, software_id="com.cursor.ide"
    )
    row = await _row_by_client_id(dcr_context, first.client_id)
    await client_svc.approve(row.id, identity=_ADMIN)

    # The lost-race arm: the row is live — the guard must not match.
    async with dcr_context.admin_db.transaction() as session:
        live = await OAuthClientRepository.get_by_id(session, row.id)
        assert live is not None
        flipped = await OAuthClientRepository.requeue_pending_if_killswitched(session, live)
        assert flipped is False
        # The instance is refreshed to the (unchanged) live state.
        assert live.approval_status == OAuthClientApprovalStatus.APPROVED.value
        assert live.active is True

    # The genuine kill-switch arm: guard matches, flips exactly once.
    await client_svc.deactivate(row.id, identity=_ADMIN)
    async with dcr_context.admin_db.transaction() as session:
        killed = await OAuthClientRepository.get_by_id(session, row.id)
        assert killed is not None
        assert await OAuthClientRepository.requeue_pending_if_killswitched(session, killed) is True
        assert killed.approval_status == OAuthClientApprovalStatus.PENDING.value
        assert killed.active is False
        # Idempotence: a second attempt finds no approved row to flip.
        assert await OAuthClientRepository.requeue_pending_if_killswitched(session, killed) is False


async def test_requeue_cas_guard_never_touches_admin_created_rows(
    dcr_context: Context, clean_dcr_tables: None
) -> None:
    """#1312 defense in depth: the re-queue CAS is pinned to DCR-sourced rows.

    Every caller reaches the CAS through the DCR-filtered dedupe candidate
    lists, but the repo guard itself must also refuse to move an
    admin-created client (approved + deactivated by an admin) back into the
    anonymous approval queue — the flip is a DCR-door semantic, not a
    general lifecycle transition."""
    async with dcr_context.admin_db.transaction() as session:
        row = await OAuthClientRepository.create(
            session,
            client_id=generate_client_id(),
            name="Platform Dashboard",
            redirect_uris=_REDIRECT_URIS,
            client_secret_hash=None,
            token_endpoint_auth_method="none",
            registration_source="admin",
            approval_status=OAuthClientApprovalStatus.APPROVED.value,
            active=False,
            created_by=_ADMIN.sub,
        )
        row_id = row.id

    async with dcr_context.admin_db.transaction() as session:
        admin_row = await OAuthClientRepository.get_by_id(session, row_id)
        assert admin_row is not None
        assert (
            await OAuthClientRepository.requeue_pending_if_killswitched(session, admin_row) is False
        )
        assert admin_row.approval_status == OAuthClientApprovalStatus.APPROVED.value
        assert admin_row.active is False
