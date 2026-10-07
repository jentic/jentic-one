"""Integration tests for the OAuth client hard delete (terminal, GitHub model).

Exercises ``OAuthClientService.delete`` against a real database (PostgreSQL or
SQLite — no mocking): the full-disconnect ordering (grants revoked + tokens
swept before the row dies), the grant-less confidential-lineage token sweep,
audit retention (grant/client history survives the delete, terminal entry
recorded), the 404 arm, the actionable-event settle, and the DCR door
afterwards — a re-registration mints a NEW pending row, never re-attaching to
the deleted one.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.admin.repos.oauth_client_grant_repo import OAuthClientGrantRepository
from jentic_one.admin.repos.refresh_token_repo import RefreshTokenRepository
from jentic_one.admin.services.errors import OAuthClientNotFoundError
from jentic_one.admin.services.oauth_client_service import (
    OAUTH_CLIENT_DELETED_REVOCATION_REASON,
    OAuthClientService,
)
from jentic_one.auth.services.oauth_dcr_service import OAuthDcrService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventType
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_hard_delete_admin", email="hard-delete-admin@test.local")
_REDIRECT_URIS = ["https://client.test.local/callback"]
_DCR_REDIRECT_URIS = ["http://localhost:33418/callback"]


@pytest.fixture()
def queue_policy_context(integration_context: Context) -> Generator[Context, None, None]:
    """Pin the DCR approval-queue policy (no auto-approve); restore after."""
    oauth_cfg = integration_context.config.server.mcp.oauth
    prior = oauth_cfg.auto_approve_clients
    oauth_cfg.auto_approve_clients = False
    yield integration_context
    oauth_cfg.auto_approve_clients = prior


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    """Remove client/grant/token rows plus their audit entries and events."""

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            result = await session.execute(select(OAuthClient.id))
            client_ids = [row[0] for row in result.all()]
            result = await session.execute(select(OAuthClientGrant.id))
            grant_ids = [row[0] for row in result.all()]
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
            await session.execute(
                delete(AccessToken).where(AccessToken.oauth_client_id.is_not(None))
            )
            await session.execute(
                delete(RefreshToken).where(RefreshToken.oauth_client_id.is_not(None))
            )
            await session.execute(delete(OAuthClientGrant))
            await session.execute(delete(OAuthClient))

    await _clean()
    yield
    await _clean()


async def _create_client(ctx: Context, *, name: str = "doomed-app") -> str:
    """Admin-create a confidential client; returns the internal row id."""
    svc = OAuthClientService(ctx)
    created = await svc.create(name=name, redirect_uris=_REDIRECT_URIS, identity=_ADMIN)
    return created.id


async def _client_row(ctx: Context, id: str) -> OAuthClient | None:
    async with ctx.admin_db.session() as session:
        return (
            await session.execute(select(OAuthClient).where(OAuthClient.id == id))
        ).scalar_one_or_none()


async def _audit_entries_for(ctx: Context, target_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry)
            .where(AuditEntry.target_id == target_id)
            .order_by(AuditEntry.occurred_at.asc(), AuditEntry.id.asc())
        )
        return list(result.scalars().all())


async def test_delete_full_disconnect_then_row_removed(
    integration_context: Context, clean_tables: None
) -> None:
    """The G11 ordering: active grants revoked + every lineage token swept,
    then the row hard-deleted — all effects visible after one service call."""
    svc = OAuthClientService(integration_context)
    created = await svc.create(name="doomed-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)

    async with integration_context.admin_db.transaction() as session:
        grant = await OAuthClientGrantRepository.create(
            session,
            oauth_client_id=created.client_id,
            user_id="usr_consenter",
            agent_id="agt_bound",
            scopes=["capabilities:read"],
            created_by="usr_consenter",
        )
        grant_id = grant.id
        expires = datetime.now(UTC) + timedelta(hours=1)
        access = await AccessTokenRepository.create(
            session,
            token_hash="at-hash-grant",
            actor_id="agt_bound",
            actor_type="agent",
            scopes=["capabilities:read"],
            token_family_id="fam-grant",
            expires_at=expires,
            created_by="usr_consenter",
            oauth_client_id=created.client_id,
            oauth_grant_id=grant_id,
        )
        refresh = await RefreshTokenRepository.create(
            session,
            token_hash="rt-hash-grant",
            actor_id="agt_bound",
            actor_type="agent",
            scopes=["capabilities:read"],
            token_family_id="fam-grant",
            expires_at=expires,
            created_by="usr_consenter",
            oauth_client_id=created.client_id,
            oauth_grant_id=grant_id,
        )
        access_id, refresh_id = access.id, refresh.id

    await svc.delete(created.id, identity=_ADMIN)

    assert await _client_row(integration_context, created.id) is None

    async with integration_context.admin_db.session() as session:
        grant_row = await OAuthClientGrantRepository.get_by_id(session, grant_id)
        assert grant_row is not None, "grant history must survive the delete"
        assert grant_row.status == OAuthGrantStatus.REVOKED.value
        assert grant_row.revoked_at is not None

        at_row = (
            await session.execute(select(AccessToken).where(AccessToken.id == access_id))
        ).scalar_one()
        rt_row = (
            await session.execute(select(RefreshToken).where(RefreshToken.id == refresh_id))
        ).scalar_one()
        assert at_row.revoked_at is not None
        assert rt_row.revoked_at is not None

    # The per-grant revocation event carries the delete cause (cause-in-data).
    async with integration_context.admin_db.session() as session:
        events = (
            (
                await session.execute(
                    select(Event).where(Event.type == EventType.OAUTH_GRANT_REVOKED)
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].data["reason"] == OAUTH_CLIENT_DELETED_REVOCATION_REASON
    assert events[0].data["grant_id"] == grant_id


async def test_delete_sweeps_grantless_client_lineage_tokens(
    integration_context: Context, clean_tables: None
) -> None:
    """Confidential ``consent_model='user'`` tokens carry client lineage but
    no grant — the delete must sweep them too (revoke_by_client arm)."""
    svc = OAuthClientService(integration_context)
    created = await svc.create(name="user-model-app", redirect_uris=_REDIRECT_URIS, identity=_ADMIN)

    async with integration_context.admin_db.transaction() as session:
        expires = datetime.now(UTC) + timedelta(hours=1)
        access = await AccessTokenRepository.create(
            session,
            token_hash="at-hash-grantless",
            actor_id="usr_direct",
            actor_type="user",
            scopes=["capabilities:read"],
            token_family_id="fam-grantless",
            expires_at=expires,
            created_by="usr_direct",
            oauth_client_id=created.client_id,
            oauth_grant_id=None,
        )
        refresh = await RefreshTokenRepository.create(
            session,
            token_hash="rt-hash-grantless",
            actor_id="usr_direct",
            actor_type="user",
            scopes=["capabilities:read"],
            token_family_id="fam-grantless",
            expires_at=expires,
            created_by="usr_direct",
            oauth_client_id=created.client_id,
            oauth_grant_id=None,
        )
        access_id, refresh_id = access.id, refresh.id

    await svc.delete(created.id, identity=_ADMIN)

    async with integration_context.admin_db.session() as session:
        at_row = (
            await session.execute(select(AccessToken).where(AccessToken.id == access_id))
        ).scalar_one()
        rt_row = (
            await session.execute(select(RefreshToken).where(RefreshToken.id == refresh_id))
        ).scalar_one()
        assert at_row.revoked_at is not None
        assert rt_row.revoked_at is not None


async def test_delete_audit_trail_survives_with_terminal_entry(
    integration_context: Context, clean_tables: None
) -> None:
    """Audit retention: pre-delete history AND the terminal delete entry both
    reference the dead client id (plain string, no FK) and survive."""
    id = await _create_client(integration_context)
    svc = OAuthClientService(integration_context)

    await svc.delete(id, identity=_ADMIN)

    entries = await _audit_entries_for(integration_context, id)
    actions = [e.action for e in entries]
    assert AuditAction.CREATE.value in actions, "pre-delete history must survive"
    assert actions[-1] == AuditAction.DELETE.value

    terminal = entries[-1]
    assert terminal.target_type == AuditTargetType.OAUTH_CLIENT.value
    assert terminal.reason == "oauth client permanently deleted"
    assert terminal.actor_id == _ADMIN.sub
    # The before snapshot preserves what was deleted; after records the sweep.
    assert terminal.before is not None
    assert terminal.before["name"] == "doomed-app"
    assert terminal.after is not None
    assert terminal.after["deleted"] is True


async def test_deactivate_now_audits_disable_not_delete(
    integration_context: Context, clean_tables: None
) -> None:
    """The reversible kill switch audits ``disable``; ``delete`` is reserved
    for the terminal arm so the two are distinguishable in the trail."""
    id = await _create_client(integration_context)
    svc = OAuthClientService(integration_context)

    await svc.deactivate(id, identity=_ADMIN)

    entries = await _audit_entries_for(integration_context, id)
    assert entries[-1].action == AuditAction.DISABLE.value
    assert entries[-1].after == {"active": False}
    assert await _client_row(integration_context, id) is not None


async def test_delete_unknown_id_raises_not_found(
    integration_context: Context, clean_tables: None
) -> None:
    svc = OAuthClientService(integration_context)
    with pytest.raises(OAuthClientNotFoundError):
        await svc.delete("oac_missing", identity=_ADMIN)


async def test_delete_pending_dcr_client_settles_actionable_event(
    queue_policy_context: Context, clean_tables: None
) -> None:
    """Deleting a pending DCR client acknowledges its live approval-queue
    alert — the dashboard must not prompt a decision on a dead row."""
    dcr = OAuthDcrService(queue_policy_context)
    result = await dcr.register(
        client_name="doomed-mcp-client",
        redirect_uris=_DCR_REDIRECT_URIS,
        token_endpoint_auth_method="none",
        software_id="com.example.doomed",
    )
    async with queue_policy_context.admin_db.session() as session:
        row = (
            await session.execute(
                select(OAuthClient).where(OAuthClient.client_id == result.client_id)
            )
        ).scalar_one()
        row_id = row.id

    svc = OAuthClientService(queue_policy_context)
    await svc.delete(row_id, identity=_ADMIN)

    async with queue_policy_context.admin_db.session() as session:
        events = (
            (
                await session.execute(
                    select(Event).where(Event.type == EventType.OAUTH_CLIENT_REGISTERED)
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].acknowledged is True
    assert events[0].acknowledged_by == _ADMIN.sub


async def test_dcr_reregister_after_delete_mints_new_pending_row(
    queue_policy_context: Context, clean_tables: None
) -> None:
    """The DCR door after a hard delete: same dedupe key (software_id +
    redirect set) lands a NEW pending row with a NEW client_id — a genuinely
    new registration, never a re-attach to (or resurrection of) the dead row."""
    dcr = OAuthDcrService(queue_policy_context)
    first = await dcr.register(
        client_name="Cursor",
        redirect_uris=_DCR_REDIRECT_URIS,
        token_endpoint_auth_method="none",
        software_id="com.cursor.ide",
    )
    assert first.created is True

    # Sanity: without a delete the same key re-attaches (created=False).
    reattach = await dcr.register(
        client_name="Cursor",
        redirect_uris=_DCR_REDIRECT_URIS,
        token_endpoint_auth_method="none",
        software_id="com.cursor.ide",
    )
    assert reattach.created is False
    assert reattach.client_id == first.client_id

    async with queue_policy_context.admin_db.session() as session:
        row = (
            await session.execute(
                select(OAuthClient).where(OAuthClient.client_id == first.client_id)
            )
        ).scalar_one()
        first_row_id = row.id

    svc = OAuthClientService(queue_policy_context)
    await svc.delete(first_row_id, identity=_ADMIN)

    second = await dcr.register(
        client_name="Cursor",
        redirect_uris=_DCR_REDIRECT_URIS,
        token_endpoint_auth_method="none",
        software_id="com.cursor.ide",
    )
    assert second.created is True, "post-delete registration must be a fresh row"
    assert second.client_id != first.client_id

    async with queue_policy_context.admin_db.session() as session:
        rows = (await session.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1
    assert rows[0].client_id == second.client_id
    assert rows[0].approval_status == "pending"
    assert rows[0].active is False
