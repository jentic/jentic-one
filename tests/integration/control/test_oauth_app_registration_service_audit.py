"""Integration tests verifying best-effort cross-DB audit for OAuthAppRegistrationService.

Each registration mutation commits against the control database and then
writes an audit entry best-effort against the admin database. These tests walk
one registration through create → update → rotate → delete and assert the
expected rows — and that the client secret, plaintext or sealed, never lands
in a recorded snapshot.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.services.oauth_app_registrations.service import (
    OAuthAppRegistrationService,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditReason, AuditTargetType

pytestmark = pytest.mark.integration

# Module-level constants rather than inline ``client_secret="..."`` so the
# detect-secrets ``SecretKeyword`` heuristic doesn't flag the call sites.
_SECRET_ORIGINAL = "audit-original-fixture"  # pragma: allowlist secret
_SECRET_ROTATED = "audit-rotated-fixture"  # pragma: allowlist secret

_ADMIN = Identity(
    sub="usr_audit_admin",
    email="admin@example.test",
    permissions=["org:admin"],
    actor_type=ActorType.USER,
)


@pytest.fixture()
async def clean_registrations(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    tables = (
        AuthorizationCodeAppRegistrationDetails,
        DeviceAuthorizationAppRegistrationDetails,
        OAuthAppRegistration,
    )

    async def _wipe() -> None:
        async with control_db.session() as session:
            for table in tables:
                await session.execute(delete(table))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


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
                AuditEntry.target_type == AuditTargetType.OAUTH_APP_REGISTRATION.value,
                AuditEntry.target_id == target_id,
            )
            .order_by(AuditEntry.occurred_at)
        )
        return list(result.scalars().all())


@pytest.mark.usefixtures("clean_registrations", "clean_audit")
async def test_lifecycle_records_one_audit_entry_per_mutation(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Audited",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid-audit",
        client_secret=_SECRET_ORIGINAL,
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=["read"],
        identity=_ADMIN,
    )
    await svc.update(reg.id, name="Audited (renamed)", is_active=False, identity=_ADMIN)
    await svc.rotate_client_secret(reg.id, client_secret=_SECRET_ROTATED, identity=_ADMIN)
    await svc.delete(reg.id, identity=_ADMIN)

    entries = await _audit_entries_for(integration_context, reg.id)
    assert [e.action for e in entries] == [
        AuditAction.CREATE.value,
        AuditAction.UPDATE.value,
        AuditAction.ROTATE.value,
        AuditAction.DELETE.value,
    ]
    assert all(e.actor_id == _ADMIN.sub for e in entries)
    assert all(e.actor_type == ActorType.USER.value for e in entries)

    create, update, rotate, delete_ = entries
    assert create.after is not None
    assert create.after["name"] == "Audited"
    assert create.after["flow_kind"] == "authorization_code"
    assert create.after["client_id"] == "cid-audit"

    assert update.before is not None
    assert update.after is not None
    assert update.before["name"] == "Audited"
    assert update.before["is_active"] is True
    assert update.after["name"] == "Audited (renamed)"
    assert update.after["is_active"] is False

    assert rotate.reason == AuditReason.CLIENT_SECRET_ROTATED.value

    assert delete_.before is not None
    assert delete_.before["name"] == "Audited (renamed)"


@pytest.mark.usefixtures("clean_registrations", "clean_audit")
async def test_audit_snapshots_never_carry_the_client_secret(
    integration_context: Context,
) -> None:
    svc = OAuthAppRegistrationService(integration_context)
    reg = await svc.create_authorization_code(
        name="Secretive",
        api_vendor="v",
        catalog_api_id="v/api",
        display_name="V",
        client_id="cid-secret",
        client_secret=_SECRET_ORIGINAL,
        authorize_url="https://ex/a",
        token_url="https://ex/t",
        default_scopes=None,
        identity=_ADMIN,
    )
    async with integration_context.control_db.session() as session:
        details = await session.get(AuthorizationCodeAppRegistrationDetails, reg.id)
    assert details is not None
    sealed_original = details.encrypted_client_secret

    await svc.rotate_client_secret(reg.id, client_secret=_SECRET_ROTATED, identity=_ADMIN)
    await svc.update(reg.id, name="Secretive (renamed)", identity=_ADMIN)
    await svc.delete(reg.id, identity=_ADMIN)

    entries = await _audit_entries_for(integration_context, reg.id)
    assert len(entries) == 4
    for entry in entries:
        recorded = f"{entry.before} {entry.after} {entry.diff} {entry.reason}"
        assert _SECRET_ORIGINAL not in recorded
        assert _SECRET_ROTATED not in recorded
        assert sealed_original not in recorded
        for snapshot in (entry.before, entry.after):
            assert not any("secret" in key for key in (snapshot or {}))
