"""Auth web tests for ``GET /agents/{id}/oauth-grants`` caller visibility.

The listing is owner-or-admin. An agent the caller cannot see answers the same
404 ``actor_not_found`` as an agent that does not exist, like every other
``/agents/{id}`` route.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import AgentRepository
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture()
async def admin_agent_id(web_context: Context, admin_user_id: str) -> AsyncGenerator[str, None]:
    """An agent owned by the admin user, outside the owner user's visibility."""
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="admin-owned-agent",
            owner_id=admin_user_id,
            registered_by=admin_user_id,
            created_by="usr_test",
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


def test_owner_lists_own_agent_grants(owner_client: TestClient, test_agent_id: str) -> None:
    resp = owner_client.get(f"/agents/{test_agent_id}/oauth-grants")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


def test_admin_lists_any_agent_grants(admin_client: TestClient, test_agent_id: str) -> None:
    resp = admin_client.get(f"/agents/{test_agent_id}/oauth-grants")
    assert resp.status_code == 200, resp.text


def test_agent_outside_caller_visibility_is_404_like_missing(
    owner_client: TestClient, admin_agent_id: str
) -> None:
    """Another user's agent and a missing agent answer the same 404, which is
    also what ``GET /agents/{id}`` answers for that agent."""
    assert owner_client.get(f"/agents/{admin_agent_id}").status_code == 404

    for agent_id in (admin_agent_id, "agnt_does_not_exist"):
        resp = owner_client.get(f"/agents/{agent_id}/oauth-grants")
        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "actor_not_found"


def test_unauthenticated_is_401(unauthed_client: TestClient, test_agent_id: str) -> None:
    assert unauthed_client.get(f"/agents/{test_agent_id}/oauth-grants").status_code == 401
