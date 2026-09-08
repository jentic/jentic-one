"""Integration tests for cross-DB credential cleanup on API delete (issue #643).

Deleting an API from the registry must deactivate the control-plane credentials
that reference it by ``(api_vendor, api_name, api_version)`` — otherwise a later
re-import plus a new credential collides with ``409 ambiguous_credential`` and
the stale binding is stranded. Registry and Control are separate databases, so
the cleanup crosses the boundary via raw SQL (no cross-module ORM import).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.services.api_service import ApiService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType

pytestmark = pytest.mark.integration

_VENDOR = "acme643.com"
_NAME = "pets-api"
_VERSION = "v1"

_IDENTITY = Identity(sub="usr_test", actor_type=ActorType.USER, permissions=["org:admin"])


@pytest.fixture()
async def clean_state(
    registry_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(Api).where(Api.vendor == _VENDOR))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api(registry_db: DatabaseSession) -> None:
    async with registry_db.session() as session:
        session.add(Api(vendor=_VENDOR, name=_NAME, version=_VERSION))
        await session.commit()


async def _seed_credential(
    control_db: DatabaseSession,
    *,
    cred_id: str,
    api_name: str | None = _NAME,
    api_version: str | None = _VERSION,
) -> None:
    async with control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name=f"cred-{cred_id}",
                api_vendor=_VENDOR,
                api_name=api_name,
                api_version=api_version,
                created_by="usr_test",
            )
        )
        await session.commit()


async def _credential_active(control_db: DatabaseSession, cred_id: str) -> bool:
    async with control_db.session() as session:
        result = await session.execute(select(Credential).where(Credential.id == cred_id))
        cred = result.scalar_one()
        return cred.active


async def test_delete_api_deactivates_matching_credential(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Deleting an API deactivates a control credential for the same identity."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_match")

    assert await _credential_active(control_db, "cred_match") is True

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    assert await _credential_active(control_db, "cred_match") is False


async def test_delete_api_leaves_other_apis_credentials_active(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Only credentials matching the deleted API identity are deactivated."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_match")
    await _seed_credential(control_db, cred_id="cred_other_version", api_version="v2")

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    assert await _credential_active(control_db, "cred_match") is False
    # A credential for a different version of the API is untouched.
    assert await _credential_active(control_db, "cred_other_version") is True
