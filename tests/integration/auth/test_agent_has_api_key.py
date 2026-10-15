"""Integration tests: ``has_api_key`` on every ``AgentResponse``.

``GET /agents`` fills the flag for a whole page in one batched lookup; ``PATCH``
and the lifecycle actions fill it for the single agent they return. Router,
service, repositories and DB are real; the only shim is the identity
dependency override (the same pattern as ``test_agent_write_authz.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from jentic_one.admin.repos import AgentCredentialRepository
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.web.deps import resolve_identity
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

OWNER = "usr_apikey_owner"


@pytest.fixture()
async def owner(integration_context: Context, clean_grants: None) -> str:
    """Seed the calling user; ``clean_grants`` removes it and its seeded agents."""
    return await seeds.seed_user(integration_context, OWNER)


@asynccontextmanager
async def _client(ctx: Context) -> AsyncIterator[AsyncClient]:
    identity = Identity(
        sub=OWNER, email=f"{OWNER}@apikey.test", permissions=["agents:read", "agents:write"]
    )
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.dependency_overrides[resolve_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _set_api_key(ctx: Context, agent_id: str, *, revoke: bool = False) -> None:
    async with ctx.admin_db.transaction() as session:
        await AgentCredentialRepository.set_api_key_hash(
            session, agent_id, api_key_hash=f"hash-{agent_id}", created_by=OWNER
        )
        if revoke:
            await AgentCredentialRepository.clear_api_key_hash(session, agent_id)


async def test_list_agents_reports_has_api_key_per_row(
    integration_context: Context, owner: str
) -> None:
    """Only the agent with an active key is ``has_api_key: true`` in the list."""
    ctx = integration_context
    keyed = await seeds.seed_agent(ctx, owner_id=owner, scopes=[], name="keyed")
    revoked = await seeds.seed_agent(ctx, owner_id=owner, scopes=[], name="revoked")
    keyless = await seeds.seed_agent(ctx, owner_id=owner, scopes=[], name="keyless")
    await _set_api_key(ctx, keyed)
    await _set_api_key(ctx, revoked, revoke=True)

    async with _client(ctx) as client:
        resp = await client.get("/agents")

    assert resp.status_code == 200, resp.text
    flags = {row["id"]: row["has_api_key"] for row in resp.json()["data"]}
    assert flags == {keyed: True, revoked: False, keyless: False}


async def test_agent_ids_with_api_key_empty_input_returns_empty_set(
    integration_context: Context,
) -> None:
    async with integration_context.admin_db.session() as session:
        assert await AgentCredentialRepository.agent_ids_with_api_key(session, []) == set()


@pytest.mark.parametrize(
    ("status", "method", "path", "body"),
    [
        (ActorStatus.ACTIVE, "PATCH", "", {"name": "renamed"}),
        (ActorStatus.PENDING, "POST", ":approve", None),
        (ActorStatus.PENDING, "POST", ":deny", {"reason": "not needed"}),
    ],
)
async def test_mutation_response_reports_has_api_key(
    integration_context: Context,
    owner: str,
    status: ActorStatus,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    """``PATCH`` and the lifecycle actions return ``has_api_key: true`` for a keyed agent."""
    ctx = integration_context
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=[], status=status)
    await _set_api_key(ctx, agent_id)

    async with _client(ctx) as client:
        resp = await client.request(method, f"/agents/{agent_id}{path}", json=body)

    assert resp.status_code == 200, resp.text
    assert resp.json()["has_api_key"] is True
