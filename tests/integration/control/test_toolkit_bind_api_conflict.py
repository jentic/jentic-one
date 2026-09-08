"""Integration tests for bind-time API conflict prevention (issue #643).

Binding a second *active* credential for the same API identity into one toolkit
produces a guaranteed-ambiguous state that the broker resolver later refuses with
``409``. ``ToolkitService.bind_credential`` refuses it up front with
``ConflictingApiBindingError`` so the operator resolves it at bind time.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.services.toolkits.errors import ConflictingApiBindingError
from jentic_one.control.services.toolkits.service import ToolkitService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_VENDOR = "acme643-bind.com"
_IDENTITY = Identity(sub="usr_test", actor_type=ActorType.USER, permissions=["org:admin"])


@pytest.fixture()
async def clean_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.execute(delete(Toolkit).where(Toolkit.name.like("tk643-%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_toolkit(control_db: DatabaseSession, name: str) -> str:
    toolkit = Toolkit(name=name)
    async with control_db.session() as session:
        session.add(toolkit)
        await session.flush()
        tk_id = toolkit.id
        await session.commit()
    return tk_id


async def _seed_credential(
    control_db: DatabaseSession,
    *,
    cred_id: str,
    api_name: str | None,
    api_version: str | None,
    active: bool = True,
) -> str:
    credential = Credential(
        id=cred_id,
        type="token_value",
        name=f"cred-{cred_id}",
        api_vendor=_VENDOR,
        api_name=api_name,
        api_version=api_version,
        active=active,
    )
    async with control_db.session() as session:
        session.add(credential)
        await session.commit()
    return cred_id


async def _bind_directly(control_db: DatabaseSession, tk_id: str, cred_id: str) -> None:
    async with control_db.session() as session:
        session.add(
            ToolkitCredentialBinding(
                id=generate_ksuid("tcb"), toolkit_id=tk_id, credential_id=cred_id
            )
        )
        await session.commit()


async def test_bind_second_credential_for_same_api_conflicts(
    integration_context: Context,
    control_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """A toolkit already bound to an active credential for the API refuses a second."""
    tk_id = await _seed_toolkit(control_db, "tk643-conflict")
    await _seed_credential(control_db, cred_id="cred_first", api_name="pets", api_version="v1")
    await _seed_credential(control_db, cred_id="cred_second", api_name="pets", api_version="v1")
    await _bind_directly(control_db, tk_id, "cred_first")

    svc = ToolkitService(integration_context)
    with pytest.raises(ConflictingApiBindingError) as exc:
        await svc.bind_credential(tk_id, "cred_second", identity=_IDENTITY)

    assert exc.value.existing_credential_id == "cred_first"


async def test_bind_credential_for_different_api_allowed(
    integration_context: Context,
    control_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """A credential for a different API version does not conflict."""
    tk_id = await _seed_toolkit(control_db, "tk643-ok")
    await _seed_credential(control_db, cred_id="cred_v1", api_name="pets", api_version="v1")
    await _seed_credential(control_db, cred_id="cred_v2", api_name="pets", api_version="v2")
    await _bind_directly(control_db, tk_id, "cred_v1")

    svc = ToolkitService(integration_context)
    result = await svc.bind_credential(tk_id, "cred_v2", identity=_IDENTITY)

    assert result.binding.credential_id == "cred_v2"


async def test_bind_credential_when_existing_is_inactive_allowed(
    integration_context: Context,
    control_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """A *deactivated* credential for the same API does not block a new binding.

    This is exactly the post-API-delete state (issue #643): the stranded
    credential is inactive, so re-binding a fresh active credential must succeed.
    """
    tk_id = await _seed_toolkit(control_db, "tk643-inactive")
    await _seed_credential(
        control_db, cred_id="cred_stale", api_name="pets", api_version="v1", active=False
    )
    await _seed_credential(control_db, cred_id="cred_fresh", api_name="pets", api_version="v1")
    await _bind_directly(control_db, tk_id, "cred_stale")

    svc = ToolkitService(integration_context)
    result = await svc.bind_credential(tk_id, "cred_fresh", identity=_IDENTITY)

    assert result.binding.credential_id == "cred_fresh"
