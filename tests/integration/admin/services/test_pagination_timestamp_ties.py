"""Keyset pagination across rows that share a timestamp (audit, users, agents).

Pins #1956: a page boundary that falls inside a run of rows with the same
``occurred_at`` / ``created_at`` must neither skip nor repeat a row. On
PostgreSQL ``now()`` is the transaction start time, so every row one
transaction writes carries the same timestamp; the rows here are written in a
single transaction with one explicit timestamp so the tie is deterministic on
both PostgreSQL and SQLite.

The shared timestamp sits far in the future so the seeded rows sort first in
the newest-first listings, ahead of anything other tests write concurrently.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.services.audit_service import AuditService
from jentic_one.admin.services.schemas.audit import AuditFilter
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db import utcnow
from jentic_one.shared.models import ActorStatus, InviteState
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.pagination import Page

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_tie_admin", email="tie-admin@test.local", permissions=["org:admin"])
_TIED_AT = datetime(2999, 1, 1, tzinfo=UTC)
_N = 5
_LIMIT = 2

_PageFetcher = Callable[[str | None], Awaitable[Page[object]]]


async def _collect_seeded(fetch: _PageFetcher, seeded: set[str]) -> list[str]:
    """Walk pages until every seeded id is seen or the listing ends; return seeded ids in order."""
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(_N + 2):
        page = await fetch(cursor)
        assert len(page.data) <= _LIMIT
        seen.extend(item.id for item in page.data if item.id in seeded)  # type: ignore[attr-defined]
        if set(seen) >= seeded or not page.has_more:
            break
        cursor = page.next_cursor
    return seen


def _assert_every_row_once(seen: list[str], seeded: set[str]) -> None:
    assert len(seen) == len(set(seen)), f"a row was returned twice: {seen}"
    assert set(seen) == seeded, f"rows skipped at a page boundary: {seeded - set(seen)}"


# --- audit ----------------------------------------------------------------------


@pytest.fixture()
async def tied_audit_ids(integration_context: Context) -> AsyncGenerator[set[str], None]:
    """``_N`` audit entries for one actor and target, written in one transaction, tied in time."""
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        entries = [
            AuditEntry(
                occurred_at=_TIED_AT,
                actor_type="user",
                actor_id="usr_tie_actor",
                action=AuditAction.CREATE,
                target_type=AuditTargetType.USER,
                target_id="usr_tie_target",
                origin="api",
            )
            for _ in range(_N)
        ]
        session.add_all(entries)
        await session.flush()
        ids = {e.id for e in entries}
    yield ids
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(AuditEntry).where(AuditEntry.id.in_(ids)))


@pytest.mark.parametrize(
    "audit_filter",
    [
        pytest.param(AuditFilter(), id="global-feed"),
        pytest.param(AuditFilter(actor_id="usr_tie_actor"), id="by-actor"),
        pytest.param(
            AuditFilter(target_type=AuditTargetType.USER, target_id="usr_tie_target"),
            id="by-target",
        ),
    ],
)
async def test_audit_pages_across_tied_occurred_at(
    integration_context: Context, tied_audit_ids: set[str], audit_filter: AuditFilter
) -> None:
    service = AuditService(integration_context)

    async def fetch(cursor: str | None) -> Page[object]:
        return await service.list_all(  # type: ignore[return-value]
            audit_filter, identity=_ADMIN, cursor=cursor, limit=_LIMIT
        )

    _assert_every_row_once(await _collect_seeded(fetch, tied_audit_ids), tied_audit_ids)


# --- users and agents -----------------------------------------------------------


@pytest.fixture()
async def tied_actors(
    integration_context: Context,
) -> AsyncGenerator[tuple[str, set[str], set[str]], None]:
    """``_N`` users and ``_N`` agents (one owner), each group tied on ``created_at``.

    Returns ``(owner_id, user_ids, agent_ids)``; ``user_ids`` includes the owner.
    """
    ctx = integration_context
    stamp = utcnow().strftime("%H%M%S%f")
    async with ctx.admin_db.transaction() as session:
        users = [
            await UserRepository.create(
                session,
                email=f"tie-{stamp}-{i}@test.local",
                first_name="Tie",
                last_name=str(i),
                invite_state=InviteState.REDEEMED,
                created_by="usr_test",
            )
            for i in range(_N)
        ]
        owner_id = users[0].id
        agents = [
            await AgentRepository.create(
                session,
                name=f"tie-agent-{stamp}-{i}",
                owner_id=owner_id,
                registered_by=owner_id,
                created_by=owner_id,
                status=ActorStatus.ACTIVE,
            )
            for i in range(_N)
        ]
        user_ids = {u.id for u in users}
        agent_ids = {a.id for a in agents}
        await session.execute(update(User).where(User.id.in_(user_ids)).values(created_at=_TIED_AT))
        await session.execute(
            update(Agent).where(Agent.id.in_(agent_ids)).values(created_at=_TIED_AT)
        )
    yield owner_id, user_ids, agent_ids
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(Agent).where(Agent.id.in_(agent_ids)))
        await session.execute(delete(User).where(User.id.in_(user_ids)))


async def test_users_page_across_tied_created_at(
    integration_context: Context, tied_actors: tuple[str, set[str], set[str]]
) -> None:
    _, user_ids, _ = tied_actors
    service = UserService(integration_context)

    async def fetch(cursor: str | None) -> Page[object]:
        return await service.list_all(  # type: ignore[return-value]
            cursor=cursor, limit=_LIMIT, identity=_ADMIN
        )

    _assert_every_row_once(await _collect_seeded(fetch, user_ids), user_ids)


@pytest.mark.parametrize("by_owner", [True, False], ids=["by-owner", "all"])
async def test_agents_page_across_tied_created_at(
    integration_context: Context, tied_actors: tuple[str, set[str], set[str]], by_owner: bool
) -> None:
    owner_id, _, agent_ids = tied_actors
    service = AgentService(integration_context)

    async def fetch(cursor: str | None) -> Page[object]:
        return await service.list_agents(  # type: ignore[return-value]
            owner_id=owner_id if by_owner else None,
            cursor=cursor,
            limit=_LIMIT,
            identity=_ADMIN,
        )

    _assert_every_row_once(await _collect_seeded(fetch, agent_ids), agent_ids)
