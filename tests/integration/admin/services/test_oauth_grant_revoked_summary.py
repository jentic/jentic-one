"""Integration tests for the ``oauth_grant.revoked`` event summary.

Exercises the single revocation body (``revoke_grant_and_sweep_tokens``) and
the admin client hard delete that calls it, against real databases: who the
summary names, and what happens to the revocation when the summary does not
fit ``Event.summary``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos.oauth_client_grant_repo import OAuthClientGrantRepository
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.auth.services.oauth_grant_service import revoke_active_grants_for_agent
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_grant_summary_admin", email="grant-summary-admin@test.local")
_REDIRECT_URIS = ["https://client.test.local/callback"]
_CLIENT_NAME = "billing-dashboard"
_CONSENTER = "usr_grant_summary_consenter"
_AGENT = "agnt_grant_summary"


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    """Clear the client/grant rows plus the audit and event rows they produce."""

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            client_ids = [row[0] for row in (await session.execute(select(OAuthClient.id))).all()]
            grant_ids = [
                row[0] for row in (await session.execute(select(OAuthClientGrant.id))).all()
            ]
            ids = client_ids + grant_ids
            if ids:
                await session.execute(delete(AuditEntry).where(AuditEntry.target_id.in_(ids)))
            await session.execute(
                delete(Event).where(
                    Event.type.in_(
                        [
                            EventType.OAUTH_CLIENT_REGISTERED,
                            EventType.OAUTH_CLIENT_APPROVED,
                            EventType.OAUTH_GRANT_REVOKED,
                        ]
                    )
                )
            )
            await session.execute(delete(OAuthClientGrant))
            await session.execute(delete(OAuthClient))

    await _clean()
    yield
    await _clean()


async def _seed_client_and_grant(ctx: Context, *, name: str) -> tuple[str, str, str]:
    """Admin-create a client and consent one grant to it.

    Returns ``(client row id, public client_id, grant id)``.
    """
    created = await OAuthClientService(ctx).create(
        name=name, redirect_uris=_REDIRECT_URIS, identity=_ADMIN
    )
    async with ctx.admin_db.transaction() as session:
        grant = await OAuthClientGrantRepository.create(
            session,
            oauth_client_id=created.client_id,
            user_id=_CONSENTER,
            agent_id=_AGENT,
            scopes=["capabilities:read"],
            created_by=_CONSENTER,
        )
        grant_id = grant.id
    return created.id, created.client_id, grant_id


async def _revoked_event(ctx: Context) -> Event | None:
    async with ctx.admin_db.session() as session:
        return (
            await session.execute(select(Event).where(Event.type == EventType.OAUTH_GRANT_REVOKED))
        ).scalar_one_or_none()


@pytest.mark.parametrize(
    ("client_name", "expected_label"),
    [
        ("billing-dashboard", "'billing-dashboard'"),
        # A quote in the name must not close the quoting and read as sentence.
        ("Ada's dashboard", "'Ada\u2019s dashboard'"),
        # A bidi override reverses every character drawn after it.
        ("billing\u202edashboard", "'billingdashboard'"),
    ],
    ids=["plain", "apostrophe", "bidi_override"],
)
async def test_grant_revocation_summary_names_the_oauth_client(
    integration_context: Context,
    clean_tables: None,
    client_name: str,
    expected_label: str,
) -> None:
    """Pins #1543: the ``oauth_grant.revoked`` summary names the OAuth client
    that lost access, and names it through ``summary_label``.

    A client id tells the reader of the activity feed nothing about which
    integration was disconnected. ``summary_label`` is the only way to
    interpolate an author-controlled name safely: it is what stops the name
    closing its own quoting, carrying invisible control characters into the
    sentence, or outgrowing ``Event.summary``. The id stays addressable in the
    event's ``data``.
    """
    client_row_id, public_client_id, grant_id = await _seed_client_and_grant(
        integration_context, name=client_name
    )

    await OAuthClientService(integration_context).delete(client_row_id, identity=_ADMIN)

    event = await _revoked_event(integration_context)
    assert event is not None, "revoking a grant records an oauth_grant.revoked event"
    assert event.data["oauth_client_id"] == public_client_id, "the id stays in data"
    assert event.summary == (
        f"OAuth grant {grant_id} for client {expected_label} "
        "was revoked because the client was deleted"
    )
    assert public_client_id not in event.summary, "the summary must not show the client as a raw id"
    assert "\u202e" not in event.summary, "control characters never reach the summary"


async def test_grant_revocation_summary_fits_the_column_at_max_length_names(
    integration_context: Context, clean_tables: None
) -> None:
    """Pins #1543: the agent sweep's summary — the only one naming two
    entities — stays inside ``Event.summary`` (``String(512)``) when both the
    agent and the client carry a 255-character name.

    ``Event.summary`` is the narrowest column the summary touches, and both
    ``agents.name`` and ``oauth_clients.name`` are ``String(255)``. An
    over-wide summary fails its INSERT, and the failed flush aborts the
    enclosing transaction — so the revocation itself is lost, not merely one
    audit event, with ``emit_event_best_effort``'s swallow hiding the cause.
    Every name the summary interpolates must therefore pass through
    ``summary_label``'s width bound.
    """
    _client_row_id, _public_client_id, grant_id = await _seed_client_and_grant(
        integration_context, name="c" * 255
    )

    async with integration_context.admin_db.transaction() as session:
        revoked = await revoke_active_grants_for_agent(
            session,
            _AGENT,
            identity=_ADMIN,
            agent_name="a" * 255,
        )
    assert revoked == 1

    async with integration_context.admin_db.session() as session:
        grant = await OAuthClientGrantRepository.get_by_id(session, grant_id)
    assert grant is not None
    assert grant.status == OAuthGrantStatus.REVOKED.value, "the revocation must persist"

    event = await _revoked_event(integration_context)
    assert event is not None, "the revocation's audit event must not be dropped"
    assert len(event.summary) <= 512
