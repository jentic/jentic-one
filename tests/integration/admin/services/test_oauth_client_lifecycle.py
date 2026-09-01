"""Integration tests for the OAuth client approval lifecycle (phase 3a-1).

Exercises OAuthClientService against a real database (PostgreSQL or SQLite —
no mocking): public secret-less creation, the D7 approve/deny lifecycle with
audit rows, the list approval_status filter, redirect-URI fingerprint
maintenance (§4.1/D8), and the migration back-compat server defaults (rows
inserted via raw SQL omitting the 3a columns read ``approved`` +
``client_secret_basic`` from the migration's server_default clauses).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, text

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos.oauth_client_repo import (
    OAuthClientRepository,
    redirect_uris_fingerprint,
)
from jentic_one.admin.services.errors import ConflictError, InvalidInputError
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_oauth_admin", email="oauth-admin@test.local")
_REDIRECT_URIS = ["https://client.test.local/callback"]


@pytest.fixture()
async def clean_oauth_clients(
    integration_context: Context,
) -> AsyncGenerator[None, None]:
    """Remove OAuth client rows (and their audit entries) before and after each test."""

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            result = await session.execute(select(OAuthClient.id))
            ids = [row[0] for row in result.all()]
            if ids:
                await session.execute(delete(AuditEntry).where(AuditEntry.target_id.in_(ids)))
            await session.execute(delete(OAuthClient))

    await _clean()
    yield
    await _clean()


async def _audit_entries_for(ctx: Context, target_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry)
            .where(
                AuditEntry.target_type == AuditTargetType.OAUTH_CLIENT.value,
                AuditEntry.target_id == target_id,
            )
            .order_by(AuditEntry.occurred_at.desc())
        )
        return list(result.scalars().all())


async def test_admin_create_defaults_are_approved_confidential(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """Admin create keeps today's behavior: approved + active, secret returned."""
    svc = OAuthClientService(integration_context)
    created = await svc.create(name="conf-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)

    assert created.approval_status == "approved"
    assert created.active is True
    assert created.token_endpoint_auth_method == "client_secret_basic"
    assert created.consent_model == "user"
    assert created.registration_source == "admin"
    assert isinstance(created.client_secret, str) and created.client_secret

    async with integration_context.admin_db.session() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.id == created.id))
        ).scalar_one()
        assert row.client_secret_hash is not None


async def test_admin_create_public_client_has_no_secret(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """Omitting the secret via token_endpoint_auth_method='none' stores NULL (D5)."""
    svc = OAuthClientService(integration_context)
    created = await svc.create(
        name="public-app",
        redirect_uris=["http://localhost:33418/callback"],
        token_endpoint_auth_method="none",
        consent_model="agent",
        identity=_ADMIN,
    )

    assert created.client_secret is None
    assert created.token_endpoint_auth_method == "none"
    assert created.consent_model == "agent"
    assert created.approval_status == "approved"

    async with integration_context.admin_db.session() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.id == created.id))
        ).scalar_one()
        assert row.client_secret_hash is None

    # A public client authenticates at the token endpoint with no secret —
    # and is rejected loudly if it supplies one.
    assert await svc.authenticate_for_token_endpoint(created.client_id, None) is True
    assert await svc.authenticate_for_token_endpoint(created.client_id, "stray") is False


async def test_deny_and_reapprove_lifecycle_with_audit(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """Deny fails the client closed everywhere, is audited, and is reversible (D7)."""
    ctx = integration_context
    svc = OAuthClientService(ctx)
    created = await svc.create(name="lifecycle-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)

    denied = await svc.deny(created.id, reason="not vetted", identity=_ADMIN)
    assert denied.approval_status == "denied"
    assert denied.active is False
    # A denied client can no longer authenticate at the token endpoint.
    assert await svc.verify_client_secret(created.client_id, "whatever") is False
    assert await svc.is_redirect_uri_allowed(created.client_id, _REDIRECT_URIS[0]) is False

    reapproved = await svc.approve(created.id, identity=_ADMIN)
    assert reapproved.approval_status == "approved"
    assert reapproved.active is True
    assert await svc.is_redirect_uri_allowed(created.client_id, _REDIRECT_URIS[0]) is True

    entries = await _audit_entries_for(ctx, created.id)
    deny_entries = [e for e in entries if e.action == AuditAction.DENY.value]
    approve_entries = [e for e in entries if e.action == AuditAction.APPROVE.value]
    assert len(deny_entries) == 1
    assert deny_entries[0].actor_id == _ADMIN.sub
    assert deny_entries[0].reason == "not vetted"
    assert deny_entries[0].after == {"approval_status": "denied", "active": False}
    assert len(approve_entries) == 1
    assert approve_entries[0].after == {"approval_status": "approved", "active": True}


async def test_list_filter_by_approval_status(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    svc = OAuthClientService(integration_context)
    kept = await svc.create(name="kept", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)
    denied = await svc.create(name="denied", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)
    await svc.deny(denied.id, identity=_ADMIN)

    approved_views = await svc.list_all(include_inactive=True, approval_status="approved")
    denied_views = await svc.list_all(include_inactive=True, approval_status="denied")
    pending_views = await svc.list_all(include_inactive=True, approval_status="pending")

    assert [v.id for v in approved_views] == [kept.id]
    assert [v.id for v in denied_views] == [denied.id]
    assert pending_views == []

    with pytest.raises(InvalidInputError):
        await svc.list_all(approval_status="bogus")


async def test_approval_queue_filter_works_without_include_inactive(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """The approval-queue query (`?approval_status=pending|denied`) must not
    silently return [] under the default active-only filter — pending/denied
    rows are active=false by construction (D7, PR #1218 MINOR-4)."""
    ctx = integration_context
    svc = OAuthClientService(ctx)

    # Seed a pending row the way the 3a-2 DCR door will (service verbs only
    # move between approved and denied).
    async with ctx.admin_db.transaction() as session:
        pending = await OAuthClientRepository.create(
            session,
            client_id="oc_pending_queue",
            name="pending-app",
            redirect_uris=_REDIRECT_URIS,
            client_secret_hash=None,
            token_endpoint_auth_method="none",
            approval_status="pending",
            active=False,
            created_by=_ADMIN.sub,
        )
        pending_id = pending.id

    denied = await svc.create(name="denied-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)
    await svc.deny(denied.id, identity=_ADMIN)

    pending_views = await svc.list_all(approval_status="pending")
    denied_views = await svc.list_all(approval_status="denied")

    assert [v.id for v in pending_views] == [pending_id]
    assert [v.id for v in denied_views] == [denied.id]


async def test_update_cannot_activate_a_denied_row(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """PATCH active=true on a denied row is rejected (409-shaped ConflictError);
    :approve is the only recovery path (D7, PR #1218 MAJOR-2)."""
    svc = OAuthClientService(integration_context)
    created = await svc.create(name="gated-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)
    await svc.deny(created.id, identity=_ADMIN)

    with pytest.raises(ConflictError, match=":approve"):
        await svc.update(created.id, active=True, identity=_ADMIN)

    # The row is untouched and the real recovery path still works.
    still_denied = await svc.get(created.id)
    assert still_denied.approval_status == "denied"
    assert still_denied.active is False
    reapproved = await svc.approve(created.id, identity=_ADMIN)
    assert reapproved.active is True


async def test_token_minted_while_approved_stops_resolving_after_deny(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """The auth-surface resolver leg of the D7 gate (PR #1218 MAJOR-1): a
    token issued while the client was approved must stop resolving once the
    row is denied — even if ``active`` is then force-set true directly in the
    DB (the state PATCH can no longer manufacture)."""
    ctx = integration_context
    svc = OAuthClientService(ctx)
    token_svc = TokenService(ctx)
    created = await svc.create(name="resolver-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)

    access_token, _refresh = await token_svc.issue_pair(
        "usr_resolver_test",
        ActorType.USER,
        ["openid"],
        oauth_client_id=created.client_id,
    )

    # While approved+active the token resolves (actor liveness is a separate
    # gate — the user row is absent here, so only the None/not-None verdict
    # of the *client* gate is under test).
    resolved = await token_svc.resolve_access_token(access_token)
    assert resolved is not None

    await svc.deny(created.id, identity=_ADMIN)
    assert await token_svc.resolve_access_token(access_token) is None

    # Force-set active=true on the denied row (no API can do this any more);
    # the resolver still fails closed on approval_status.
    async with ctx.admin_db.transaction() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.id == created.id))
        ).scalar_one()
        row.active = True
    assert await token_svc.resolve_access_token(access_token) is None

    introspected = await token_svc.introspect(access_token)
    assert introspected["active"] is False


async def test_redirect_uris_fingerprint_maintained_on_create_and_update(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """§4.1: the D8 dedupe fingerprint is written on create and kept in sync
    whenever redirect_uris changes."""
    ctx = integration_context
    svc = OAuthClientService(ctx)
    created = await svc.create(name="fp-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)

    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.id == created.id))
        ).scalar_one()
        assert row.redirect_uris_fingerprint == redirect_uris_fingerprint(_REDIRECT_URIS)

    new_uris = ["https://client.test.local/callback", "https://other.test.local/cb"]
    await svc.update(created.id, redirect_uris=new_uris, identity=_ADMIN)

    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.id == created.id))
        ).scalar_one()
        assert row.redirect_uris_fingerprint == redirect_uris_fingerprint(new_uris)
        # Order-insensitive: the reversed set is the same fingerprint (D8).
        assert row.redirect_uris_fingerprint == redirect_uris_fingerprint(list(reversed(new_uris)))


async def test_pre_3a_row_reads_migration_defaults(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """A row inserted via raw SQL omitting every 3a-1 column (the shape of a
    pre-upgrade row) must read ``approved`` + ``client_secret_basic`` etc.
    Raw ``text()`` SQL bypasses the ORM's Python-side ``default=`` values, so
    this actually exercises the ``server_default`` clauses the migration
    installed — the upgrade back-compat contract (§4.1, design §9)."""
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            uris_expr = "CAST(:uris AS varchar[])"
            uris_value = "{https://client.test.local/callback}"
        else:
            uris_expr = ":uris"
            uris_value = '["https://client.test.local/callback"]'
        await session.execute(
            text(
                "INSERT INTO oauth_clients"
                " (id, client_id, client_secret_hash, name, redirect_uris)"
                f" VALUES (:id, :client_id, :secret_hash, :name, {uris_expr})"
            ),
            {
                "id": "oac_pre3a_row_000000000000",
                "client_id": "oc_pre3a_client",
                "secret_hash": "x" * 32,
                "name": "pre-3a-app",
                "uris": uris_value,
            },
        )

    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(
                select(OAuthClient).where(OAuthClient.client_id == "oc_pre3a_client")
            )
        ).scalar_one()

    assert row.approval_status == "approved"
    assert row.token_endpoint_auth_method == "client_secret_basic"
    assert row.consent_model == "user"
    assert row.registration_source == "admin"
    assert row.software_id is None
    # Pre-3a rows carry no fingerprint until their redirect_uris are next
    # written; they have no software_id either, so they never dedupe (§4.1).
    assert row.redirect_uris_fingerprint is None
    assert row.active is True
