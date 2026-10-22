"""Limits: an agent cannot flood its owner's inbox.

Extends ``test_connect_session_api_targets.py`` (the caps at 1) with the
shipped defaults (10 per agent, 50 per owner) over HTTP and the cap holding
under concurrent asks.
"""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.services.integrations.connect_session_service import ApiTarget
from jentic_one.control.services.integrations.errors import (
    TooManyOpenSessionsError,
)
from jentic_one.shared.config import ControlConnectConfig
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    NAME,
    OWNER,
    SIBLING_AGENT_ID,
    VENDOR,
    VERSION,
    client,
    connect,
    import_spec,
    svc,
)

pytestmark = pytest.mark.integration


def _target(i: int) -> ApiTarget:
    return ApiTarget(vendor=VENDOR, name=f"{NAME}-{i}", version=VERSION)


async def _import_many(ctx: Context, count: int) -> None:
    for i in range(count):
        await import_spec(ctx, KEY_SCHEME, name=f"{NAME}-{i}")


async def _open_for(ctx: Context, agent_id: str) -> int:
    async with ctx.control_db.session() as session:
        return await ConnectSessionRepository.count_open(session, agent_ids=[agent_id])


def test_the_shipped_defaults() -> None:
    defaults = ControlConnectConfig()
    assert (defaults.max_open_sessions_per_agent, defaults.max_open_sessions_per_owner) == (10, 50)


async def test_per_agent_cap_of_ten_is_429_over_http(env: Context) -> None:
    await _import_many(env, 11)
    for i in range(10):
        await connect(env, target=_target(i))
    async with client(env, AGENT) as agent:
        refused = await agent.post(
            "/integrations:connect",
            json={"api": {"vendor": VENDOR, "name": f"{NAME}-10", "version": VERSION}},
        )
    assert (refused.status_code, refused.json()["type"]) == (429, "too_many_open_sessions")
    assert await _open_for(env, AGENT_ID) == 10
    # A sibling agent of the same owner has its own allowance.
    assert (await connect(env, agent_id=SIBLING_AGENT_ID, target=_target(10))).session_id


async def test_per_owner_cap_of_fifty_counts_the_owners_and_their_agents_sessions(
    env: Context,
) -> None:
    await _import_many(env, 1)
    await import_spec(env, KEY_SCHEME)
    for _ in range(50):
        await svc(env).create_session(
            vendor_key="", agent_id=None, identity=OWNER, api_target=_target(0)
        )
    with pytest.raises(TooManyOpenSessionsError) as owner_full:
        await svc(env).create_session(
            vendor_key="", agent_id=None, identity=OWNER, api_target=_target(0)
        )
    assert (owner_full.value.scope, owner_full.value.limit) == ("owner", 50)
    # Their agent's ask counts against the same owner.
    async with client(env, AGENT) as agent:
        refused = await agent.post("/integrations:connect", json=API_BODY)
    assert (refused.status_code, refused.json()["type"]) == (429, "too_many_open_sessions")


async def test_per_agent_cap_holds_under_concurrent_asks(env: Context) -> None:
    await _import_many(env, 16)
    outcomes = await asyncio.gather(
        *(connect(env, target=_target(i)) for i in range(16)), return_exceptions=True
    )
    refused = [o for o in outcomes if isinstance(o, TooManyOpenSessionsError)]
    unexpected = [o for o in outcomes if isinstance(o, BaseException) and o not in refused]
    assert not unexpected, unexpected
    assert await _open_for(env, AGENT_ID) <= 10
    assert len(refused) >= 6
