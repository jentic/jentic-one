"""Integration tests for the OAuth client approval lifecycle (phase 3a-1).

Exercises OAuthClientService against a real database (PostgreSQL or SQLite —
no mocking): public secret-less creation, the D7 approve/deny lifecycle with
audit rows, the list approval_status filter, and the migration back-compat
server defaults (rows inserted without the 3a columns read ``approved`` +
``client_secret_basic``).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import delete, insert, select

if TYPE_CHECKING:
    from sqlalchemy import Table

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
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


async def test_pre_3a_row_reads_migration_defaults(
    integration_context: Context, clean_oauth_clients: None
) -> None:
    """A row inserted without any 3a-1 column (the shape of every pre-upgrade
    row) reads ``approved`` + ``client_secret_basic`` etc. (§4.1). The model's
    Python defaults and the migration's server defaults are defined to the same
    values, so this guards both against drift."""
    ctx = integration_context
    oauth_clients = cast("Table", OAuthClient.__table__)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            insert(oauth_clients).values(
                id="oac_pre3a_row_000000000000",
                client_id="oc_pre3a_client",
                client_secret_hash="x" * 32,
                name="pre-3a-app",
                redirect_uris=_REDIRECT_URIS,
            )
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
    assert row.active is True
