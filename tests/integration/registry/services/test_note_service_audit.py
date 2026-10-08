"""Integration tests verifying best-effort cross-DB audit for NoteService.

NoteService mutates the registry database, while audit entries are written
best-effort against the admin database. These tests assert the expected audit
rows land for the note create/update/delete lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.services.note_service import NoteService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration

_IDENTITY = Identity(sub="usr_test", email="test@example.com")


@pytest.fixture()
async def clean_audit(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AuditEntry))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def _audit_entries_for(ctx: Context, target_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry)
            .where(
                AuditEntry.target_type == AuditTargetType.NOTE.value,
                AuditEntry.target_id == target_id,
            )
            .order_by(AuditEntry.occurred_at.desc())
        )
        return list(result.scalars().all())


async def test_create_records_audit_entry(
    integration_context: Context,
    note_api: Api,
    clean_audit: None,
) -> None:
    svc = NoteService(integration_context)
    view = await svc.create(
        resource_api=(note_api.vendor, note_api.name, note_api.version),
        body="An audited note",
        identity=_IDENTITY,
    )

    entries = await _audit_entries_for(integration_context, view.id)
    create_entries = [e for e in entries if e.action == AuditAction.CREATE.value]
    assert len(create_entries) == 1
    assert create_entries[0].target_type == AuditTargetType.NOTE.value
    assert create_entries[0].target_id == view.id


async def test_update_and_delete_record_audit_entries(
    integration_context: Context,
    note_api: Api,
    clean_audit: None,
) -> None:
    svc = NoteService(integration_context)
    view = await svc.create(
        resource_api=(note_api.vendor, note_api.name, note_api.version),
        body="Original body",
        identity=_IDENTITY,
    )

    await svc.update(view.id, body="Updated body", identity=_IDENTITY)
    await svc.delete(view.id, identity=_IDENTITY)

    entries = await _audit_entries_for(integration_context, view.id)
    actions = {e.action for e in entries}
    assert AuditAction.CREATE.value in actions
    assert AuditAction.UPDATE.value in actions
    assert AuditAction.DELETE.value in actions
