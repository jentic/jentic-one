"""Integration tests for AuditService against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.repos import AuditRepository
from jentic_one.admin.services.audit_service import AuditService
from jentic_one.admin.services.errors import AuditEntryNotFoundError, InvalidInputError
from jentic_one.admin.services.schemas.audit import AuditFilter
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration

# Admin bypasses scoping; these tests cover the filter plumbing, not visibility
# (see ``test_audit_scoping.py``).
_ADMIN = Identity(sub="usr_audit_admin", email="admin@test.local", permissions=["org:admin"])


@pytest.fixture()
async def clean_audit(integration_context: Context) -> None:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(AuditEntry))
        await session.commit()


async def test_list_pagination(integration_context: Context, clean_audit: None) -> None:
    ctx = integration_context
    for i in range(5):
        async with ctx.admin_db.transaction() as session:
            await AuditRepository.record(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.USER,
                target_id=f"user-{i}",
                actor_type="user",
                actor_id="actor-x",
            )

    service = AuditService(ctx)
    page1 = await service.list_all(AuditFilter(), identity=_ADMIN, limit=2)
    assert len(page1.data) == 2
    assert page1.has_more is True
    assert page1.next_cursor is not None

    page2 = await service.list_all(
        AuditFilter(), identity=_ADMIN, cursor=page1.next_cursor, limit=2
    )
    assert len(page2.data) == 2
    assert page2.has_more is True


async def test_list_by_target(integration_context: Context, clean_audit: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        await AuditRepository.record(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.USER,
            target_id="user-target",
            actor_type="user",
            actor_id="actor-1",
        )
        await AuditRepository.record(
            session,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.JOB,
            target_id="job-other",
            actor_type="agent",
            actor_id="actor-2",
        )

    service = AuditService(ctx)
    page = await service.list_all(
        AuditFilter(target_type=AuditTargetType.USER, target_id="user-target"), identity=_ADMIN
    )
    assert len(page.data) == 1
    assert page.data[0].target_type == "user"
    assert page.data[0].target_id == "user-target"


async def test_list_by_actor(integration_context: Context, clean_audit: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        await AuditRepository.record(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.USER,
            target_id="u1",
            actor_type="user",
            actor_id="actor-alpha",
        )
        await AuditRepository.record(
            session,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.USER,
            target_id="u2",
            actor_type="user",
            actor_id="actor-beta",
        )

    service = AuditService(ctx)
    page = await service.list_all(AuditFilter(actor_id="actor-alpha"), identity=_ADMIN)
    assert len(page.data) == 1
    assert page.data[0].actor_id == "actor-alpha"


async def test_list_combined_filter(integration_context: Context, clean_audit: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        await AuditRepository.record(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.USER,
            target_id="u-combo",
            actor_type="user",
            actor_id="actor-match",
        )
        await AuditRepository.record(
            session,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.USER,
            target_id="u-combo",
            actor_type="user",
            actor_id="actor-other",
        )

    service = AuditService(ctx)
    page = await service.list_all(
        AuditFilter(
            target_type=AuditTargetType.USER,
            target_id="u-combo",
            actor_id="actor-match",
        ),
        identity=_ADMIN,
    )
    assert len(page.data) == 1
    assert page.data[0].actor_id == "actor-match"


async def test_list_since_until(integration_context: Context, clean_audit: None) -> None:
    ctx = integration_context
    now = datetime.now(tz=UTC)
    async with ctx.admin_db.transaction() as session:
        await AuditRepository.record(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.USER,
            target_id="u-time",
            actor_type="user",
            actor_id="actor-t",
        )

    service = AuditService(ctx)
    page = await service.list_all(AuditFilter(since=now - timedelta(minutes=1)), identity=_ADMIN)
    assert len(page.data) >= 1

    page_future = await service.list_all(
        AuditFilter(since=now + timedelta(hours=1)), identity=_ADMIN
    )
    assert len(page_future.data) == 0


async def test_target_type_xor_target_id_raises(
    integration_context: Context, clean_audit: None
) -> None:
    service = AuditService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.list_all(AuditFilter(target_type=AuditTargetType.USER), identity=_ADMIN)

    with pytest.raises(InvalidInputError):
        await service.list_all(AuditFilter(target_id="some-id"), identity=_ADMIN)


async def test_get_by_id_found(integration_context: Context, clean_audit: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        entry = await AuditRepository.record(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.USER,
            target_id="u-get",
            actor_type="user",
            actor_id="actor-g",
            reason="test reason",
        )
    entry_id = entry.id

    service = AuditService(ctx)
    view = await service.get_by_id(entry_id, identity=_ADMIN)
    assert view.id == entry_id
    assert view.action == "create"
    assert view.target_type == "user"
    assert view.target_id == "u-get"
    assert view.actor_id == "actor-g"
    assert view.reason == "test reason"


async def test_get_by_id_not_found(integration_context: Context, clean_audit: None) -> None:
    service = AuditService(integration_context)
    with pytest.raises(AuditEntryNotFoundError):
        await service.get_by_id("aud_nonexistent0000000000000", identity=_ADMIN)
