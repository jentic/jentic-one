"""Auth web tests for the OAuth grant routes' caller visibility.

``GET /agents/{id}/oauth-grants`` is owner-or-admin. An agent the caller cannot
see answers the same 404 ``actor_not_found`` as an agent that does not exist,
like every other ``/agents/{id}`` route.

``POST /oauth-grants/{id}:revoke`` answers a grant the caller cannot see with
the same 404 ``oauth_grant_not_found`` as an unknown grant id; only a caller
who can see the grant but may not revoke it gets 403.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos import (
    AgentRepository,
    OAuthClientGrantRepository,
    OAuthClientRepository,
)
from jentic_one.shared.context import Context
from tests.web.auth.conftest import _build_app, _make_agent_token, assert_same_response

pytestmark = pytest.mark.integration

_CLIENT_ID = "oc_web_grant_visibility"


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


@pytest.fixture()
async def oauth_client_id(web_context: Context) -> AsyncGenerator[str, None]:
    """An approved public OAuth client the grants below are issued to."""
    async with web_context.admin_db.transaction() as session:
        await OAuthClientRepository.create(
            session,
            client_id=_CLIENT_ID,
            name="Grant Visibility App",
            redirect_uris=["https://app.example.com/cb"],
            client_secret_hash=None,
            token_endpoint_auth_method="none",
            consent_model="agent",
            created_by="usr_test",
        )
    yield _CLIENT_ID

    async with web_context.admin_db.session() as session:
        await session.execute(
            delete(OAuthClientGrant).where(OAuthClientGrant.oauth_client_id == _CLIENT_ID)
        )
        await session.execute(delete(OAuthClient).where(OAuthClient.client_id == _CLIENT_ID))
        await session.commit()


async def _create_grant(ctx: Context, *, client_id: str, user_id: str, agent_id: str) -> str:
    async with ctx.admin_db.transaction() as session:
        grant = await OAuthClientGrantRepository.create(
            session,
            oauth_client_id=client_id,
            user_id=user_id,
            agent_id=agent_id,
            scopes=["apis:read"],
            created_by="usr_test",
        )
    return grant.id


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


@pytest.fixture()
async def sibling_agent_id(web_context: Context, owner_user_id: str) -> AsyncGenerator[str, None]:
    """A second agent of the owner user, a sibling of ``test_agent_id``."""
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="sibling-agent",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


@pytest.mark.parametrize("caller", ["sibling", "self"])
def test_agent_caller_who_does_not_own_the_agent_matches_unknown_id(
    web_context: Context,
    owner_client: TestClient,
    owner_user_id: str,
    test_agent_id: str,
    sibling_agent_id: str,
    caller: str,
) -> None:
    """A caller who can see the agent without owning it gets the unknown-agent 404.

    That covers a sibling agent reading through owner delegation and the
    agent itself. The owner still lists the agent's grants.
    """
    caller_id = sibling_agent_id if caller == "sibling" else test_agent_id
    token = _make_agent_token(web_context, caller_id, owner_user_id, ["owner:agents:read"])
    unknown_id = "agnt_does_not_exist"
    with TestClient(
        _build_app(web_context), headers={"Authorization": f"Bearer {token}"}
    ) as agent_client:
        foreign = agent_client.get(f"/agents/{test_agent_id}/oauth-grants")
        unknown = agent_client.get(f"/agents/{unknown_id}/oauth-grants")
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["type"] == "actor_not_found"
    assert_same_response(foreign, test_agent_id, unknown, unknown_id)

    assert owner_client.get(f"/agents/{test_agent_id}/oauth-grants").status_code == 200


def test_unauthenticated_is_401(unauthed_client: TestClient, test_agent_id: str) -> None:
    assert unauthed_client.get(f"/agents/{test_agent_id}/oauth-grants").status_code == 401


async def test_revoke_grant_caller_cannot_see_matches_unknown_id(
    web_context: Context,
    owner_client: TestClient,
    admin_client: TestClient,
    owner_user_id: str,
    admin_user_id: str,
    test_agent_id: str,
    admin_agent_id: str,
    oauth_client_id: str,
) -> None:
    """A grant outside the caller's rows answers exactly like an unknown id.

    The same caller still revokes a grant it consented to, and an admin
    still revokes the other one.
    """
    grant_id = await _create_grant(
        web_context, client_id=oauth_client_id, user_id=admin_user_id, agent_id=admin_agent_id
    )
    own_grant_id = await _create_grant(
        web_context, client_id=oauth_client_id, user_id=owner_user_id, agent_id=test_agent_id
    )
    unknown_id = "ocg_does_not_exist"

    foreign = owner_client.post(f"/oauth-grants/{grant_id}:revoke")
    unknown = owner_client.post(f"/oauth-grants/{unknown_id}:revoke")
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["type"] == "oauth_grant_not_found"
    assert_same_response(foreign, grant_id, unknown, unknown_id)

    assert owner_client.post(f"/oauth-grants/{own_grant_id}:revoke").status_code == 204
    assert admin_client.post(f"/oauth-grants/{grant_id}:revoke").status_code == 204


async def test_revoke_grant_visible_but_not_consented_is_403(
    web_context: Context,
    owner_client: TestClient,
    admin_user_id: str,
    test_agent_id: str,
    oauth_client_id: str,
) -> None:
    """The agent's owner sees a grant someone else consented to, but may not revoke it."""
    grant_id = await _create_grant(
        web_context, client_id=oauth_client_id, user_id=admin_user_id, agent_id=test_agent_id
    )
    resp = owner_client.post(f"/oauth-grants/{grant_id}:revoke")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "oauth_grant_access_denied"
