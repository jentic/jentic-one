"""PostgreSQL concurrency coverage for API connect creation and identity repair."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.credential_identity_repair import (
    CredentialIdentityRepairService,
)
from jentic_one.control.services.integrations.connect_session_service import ApiTarget
from jentic_one.control.services.integrations.errors import UnknownApiError
from jentic_one.registry.core.schema.apis import Api
from jentic_one.shared.context import Context
from jentic_one.shared.models.credential_identity_repair import RepairRunResult
from tests.integration.control.test_connect_session_api_targets import (
    _AGENT_ID,
    _CATALOG_ID,
    _KEY_SCHEME,
    _NAME,
    _RULES,
    _VENDOR,
    _VERSION,
    _agent,
    _import,
    _svc,
    env,
)

__all__ = ("env",)

pytestmark = pytest.mark.integration


async def _wait_for_connect_lock(ctx: Context) -> None:
    """Wait until the real create transaction is queued on the repair advisory lock."""
    async with asyncio.timeout(10):
        while True:
            async with ctx.control_db.session() as session:
                waiting = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                            "AND wait_event_type = 'Lock' "
                            "AND query LIKE '%pg_advisory_xact_lock%' LIMIT 1"
                        )
                    )
                ).first()
            if waiting is not None:
                return
            await asyncio.sleep(0.01)


async def test_api_connect_revalidates_after_identity_repair_releases_lock(
    env: Context,
) -> None:
    """A creator that read the old identity cannot commit it after the repair commits."""
    if env.control_db.engine.dialect.name != "postgresql":
        pytest.skip("Advisory-lock interleaving requires PostgreSQL")

    malformed_name = f"{_VENDOR}-{_NAME}"
    await _import(env, _KEY_SCHEME, name=malformed_name)
    async with env.registry_db.transaction() as session:
        api = (await session.execute(select(Api).where(Api.vendor == _VENDOR))).scalar_one()
        api.catalog_api_id = _CATALOG_ID

    repair_started = asyncio.Event()
    finish_repair = asyncio.Event()

    async def pause_before_apply(_planned: RepairRunResult) -> None:
        repair_started.set()
        await finish_repair.wait()

    repair_task = asyncio.create_task(
        CredentialIdentityRepairService(env).run(before_apply=pause_before_apply)
    )
    async with asyncio.timeout(10):
        await repair_started.wait()

    target = ApiTarget(vendor=_VENDOR, name=malformed_name, version=_VERSION)
    create_task = asyncio.create_task(
        _svc(env).create_session(
            vendor_key="",
            agent_id=_AGENT_ID,
            identity=_agent(_AGENT_ID),
            api_target=target,
            requested_permission_rules=_RULES,
            reason="race identity repair",
        )
    )
    try:
        await _wait_for_connect_lock(env)
    finally:
        finish_repair.set()

    repaired = await repair_task
    assert repaired.failed_closed is False
    assert repaired.repaired_apis == 1
    with pytest.raises(UnknownApiError):
        await create_task

    async with env.control_db.session() as session:
        assert (await session.execute(select(ConnectSession))).scalars().all() == []
        assert (await session.execute(select(Credential))).scalars().all() == []
