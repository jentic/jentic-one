"""``last_used_at`` restarts a binding's idle clock, written at most once per interval.

A binding with a use count gives up one use per call, atomically.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.broker.repos.binding_use_repo import BindingUseRepository
from jentic_one.shared.auth.expiry import as_utc
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_AGENT = "agnt_use_repo"
_CRED = "cred_use_repo"
_HOUR = timedelta(hours=1)
_T0 = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


@pytest.fixture()
async def binding(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.transaction() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == _AGENT)
        )
        session.add(
            AgentCredentialBinding(
                id="acb_use_repo", agent_id=_AGENT, credential_id=_CRED, max_idle_period=86_400
            )
        )
    yield
    async with admin_db.transaction() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == _AGENT)
        )


async def _record(admin_db: DatabaseSession, now: datetime) -> bool:
    async with admin_db.transaction() as session:
        return await BindingUseRepository.record_use(
            session, agent_id=_AGENT, credential_id=_CRED, now=now, min_interval=_HOUR
        )


async def _last_used(admin_db: DatabaseSession) -> datetime | None:
    async with admin_db.session() as session:
        value = await session.scalar(
            select(AgentCredentialBinding.last_used_at).where(
                AgentCredentialBinding.agent_id == _AGENT
            )
        )
    return as_utc(value) if value is not None else None


async def test_first_use_is_stamped(admin_db: DatabaseSession, binding: None) -> None:
    assert await _record(admin_db, _T0) is True
    assert await _last_used(admin_db) == _T0


async def test_a_use_within_the_interval_writes_nothing(
    admin_db: DatabaseSession, binding: None
) -> None:
    await _record(admin_db, _T0)
    assert await _record(admin_db, _T0 + timedelta(minutes=59)) is False
    assert await _last_used(admin_db) == _T0


async def test_a_use_after_the_interval_moves_the_stamp(
    admin_db: DatabaseSession, binding: None
) -> None:
    await _record(admin_db, _T0)
    assert await _record(admin_db, _T0 + _HOUR) is True
    assert await _last_used(admin_db) == _T0 + _HOUR


async def test_a_binding_the_agent_does_not_hold_changes_nothing(
    admin_db: DatabaseSession, binding: None
) -> None:
    async with admin_db.transaction() as session:
        changed = await BindingUseRepository.record_use(
            session, agent_id=_AGENT, credential_id="cred_other", now=_T0, min_interval=_HOUR
        )
    assert changed is False


async def _take(admin_db: DatabaseSession, now: datetime = _T0) -> bool:
    async with admin_db.transaction() as session:
        return await BindingUseRepository.take_use(
            session, agent_id=_AGENT, credential_id=_CRED, now=now
        )


async def _uses_left(admin_db: DatabaseSession) -> int | None:
    async with admin_db.session() as session:
        return await session.scalar(
            select(AgentCredentialBinding.quota_remaining).where(
                AgentCredentialBinding.agent_id == _AGENT
            )
        )


async def test_a_binding_without_a_count_allows_and_writes_nothing(
    admin_db: DatabaseSession, binding: None
) -> None:
    assert await _take(admin_db) is True
    assert await _uses_left(admin_db) is None
    assert await _last_used(admin_db) is None


async def test_single_use_allows_once_and_stamps_the_use(
    admin_db: DatabaseSession, binding: None
) -> None:
    async with admin_db.transaction() as session:
        await session.execute(
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == _AGENT)
            .values(max_quota=1, quota_remaining=1)
        )
    assert await _take(admin_db) is True
    assert await _uses_left(admin_db) == 0
    assert await _last_used(admin_db) == _T0
    # The last use is gone: the next call gets nothing, and the count stays at zero.
    assert await _take(admin_db, _T0 + timedelta(minutes=1)) is False
    assert await _uses_left(admin_db) == 0


async def test_a_binding_the_agent_does_not_hold_has_no_count_to_take(
    admin_db: DatabaseSession, binding: None
) -> None:
    async with admin_db.transaction() as session:
        allowed = await BindingUseRepository.take_use(
            session, agent_id=_AGENT, credential_id="cred_other", now=_T0
        )
    assert allowed is True
