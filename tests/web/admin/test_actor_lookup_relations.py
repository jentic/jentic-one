"""Web tests for the ``GET /actors/lookup`` user relations beyond self.

Each relation labels a user only when the caller can already see the object
that names them, through the read permission of the route that shows it:

- agent ``registered_by`` / ``denied_by`` (with ``owner_id`` and
  ``approved_by``): an agent the caller can see, with ``agents:read``;
- held execution ``decided_by``: a held execution the caller can see (the
  holds routes take no permission);
- OAuth grant ``user_id``: a grant the caller can see, with
  ``oauth-clients:read``;
- OAuth client ``created_by``: any client, with ``oauth-clients:read``.

For each: the related user resolves; the same relation on an object the
caller cannot see, or without the permission, answers byte-for-byte like an
id that does not exist.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.repos.oauth_client_grant_repo import OAuthClientGrantRepository
from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, InviteState, JobKind, JobStatus

pytestmark = pytest.mark.integration

_UNKNOWN_USER = "usr_does_not_exist"
_MEMBER_PERMISSIONS = ("agents:read", "oauth-clients:read")


@dataclass(frozen=True)
class _World:
    member_id: str
    other_owner_id: str
    #: the member's active agent, which filed the member's held execution
    member_agent_id: str
    #: relation -> (user named on an object the member sees, same on one it does not)
    users: dict[str, tuple[str, str]]


_RELATIONS = ("registered_by", "denied_by", "decided_by", "consented_by", "client_creator")


async def _user(ctx: Context, key: str) -> str:
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email=f"lookup-rel-{key}-{uuid.uuid4().hex[:8]}@test.local",
            first_name="Rel",
            last_name=key,
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await session.commit()
        return user.id


@pytest.fixture()
async def world(web_context: Context) -> AsyncGenerator[_World, None]:
    """One visible and one hidden object per relation.

    The member owns ``mine`` (registered by one user) and ``mine_denied``
    (denied by another); ``theirs`` and ``theirs_denied`` belong to another
    owner. Each pair of agents carries a held execution decided by a different
    user and an OAuth grant consented by a different user. The OAuth client
    created by ``client_creator`` is visible to any ``oauth-clients:read``
    holder, so its hidden twin is "no such client" (the user created nothing).
    """
    ctx = web_context
    member_id = await _user(ctx, "member")
    other_owner_id = await _user(ctx, "other-owner")
    users = {
        rel: (await _user(ctx, f"{rel}-seen"), await _user(ctx, f"{rel}-hidden"))
        for rel in _RELATIONS
    }
    agent_ids: list[str] = []
    filers: list[str] = []
    job_ids: list[str] = []
    grant_ids: list[str] = []
    client_ids: list[str] = []
    async with ctx.admin_db.session() as session:
        for seen, owner in ((0, member_id), (1, other_owner_id)):
            registered = await AgentRepository.create(
                session,
                name=f"lookup-rel-registered-{seen}",
                owner_id=owner,
                registered_by=users["registered_by"][seen],
                created_by=owner,
                status=ActorStatus.ACTIVE,
            )
            denied = await AgentRepository.create(
                session,
                name=f"lookup-rel-denied-{seen}",
                owner_id=owner,
                registered_by=owner,
                created_by=owner,
                status=ActorStatus.REJECTED,
            )
            denied.denied_by = users["denied_by"][seen]
            agent_ids += [registered.id, denied.id]
            filers.append(registered.id)

            job = Job(
                kind=JobKind.EXECUTION,
                status=JobStatus.FAILED,
                created_by=registered.id,
                actor_type="agent",
            )
            session.add(job)
            await session.flush()
            job_ids.append(job.id)
            session.add(
                HeldExecution(
                    job_id=job.id,
                    agent_id=registered.id,
                    credential_id="cred_lookup_rel",
                    api_vendor="api.example.com",
                    api_name="main",
                    api_version="1",
                    method="POST",
                    path="/v1/x",
                    request_fingerprint=job.id.ljust(64, "0"),
                    state="denied",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    decided_at=datetime.now(UTC),
                    decided_by=users["decided_by"][seen],
                )
            )

            grant = await OAuthClientGrantRepository.create(
                session,
                oauth_client_id=f"lookup-rel-client-{seen}",
                user_id=users["consented_by"][seen],
                agent_id=registered.id,
                scopes=["capabilities:read"],
                created_by=users["consented_by"][seen],
            )
            grant_ids.append(grant.id)

        client = await OAuthClientRepository.create(
            session,
            client_id=f"lookup-rel-{uuid.uuid4().hex[:8]}",
            name="lookup-rel-client",
            redirect_uris=["https://lookup-rel.example.com/cb"],
            client_secret_hash=None,
            created_by=users["client_creator"][0],
        )
        client_ids.append(client.id)
        await session.commit()

    yield _World(
        member_id=member_id,
        other_owner_id=other_owner_id,
        member_agent_id=filers[0],
        users=users,
    )

    all_users = [member_id, other_owner_id, *(u for pair in users.values() for u in pair)]
    async with ctx.admin_db.session() as session:
        await session.execute(delete(OAuthClient).where(OAuthClient.id.in_(client_ids)))
        await session.execute(delete(OAuthClientGrant).where(OAuthClientGrant.id.in_(grant_ids)))
        await session.execute(delete(HeldExecution).where(HeldExecution.job_id.in_(job_ids)))
        await session.execute(delete(Job).where(Job.id.in_(job_ids)))
        await session.execute(delete(Agent).where(Agent.id.in_(agent_ids)))
        await session.execute(delete(User).where(User.id.in_(all_users)))
        await session.commit()


def _headers(
    ctx: Context,
    user_id: str,
    permissions: tuple[str, ...],
    *,
    agent_of: str | None = None,
) -> dict[str, str]:
    """Bearer header for a user, or for an agent of ``agent_of`` when given."""
    config = ctx.config.admin.auth
    claims: dict[str, object] = {
        "sub": user_id,
        "email": "" if agent_of else "lookup-rel-member@test.local",
        "actor_type": "agent" if agent_of else "user",
        "permissions": list(permissions),
        "must_change_password": False,
    }
    if agent_of:
        claims["parent_actor_id"] = agent_of
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def _lookup(client: TestClient, headers: dict[str, str], user_id: str) -> bytes:
    resp = client.get("/actors/lookup", params={"id": user_id}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.content


#: The permission each relation needs; ``None`` when its route takes none.
_GATE: dict[str, str | None] = {
    "registered_by": "agents:read",
    "denied_by": "agents:read",
    "decided_by": None,
    "consented_by": "oauth-clients:read",
    "client_creator": "oauth-clients:read",
}


@pytest.mark.parametrize("relation", _RELATIONS)
def test_relation_resolves_the_user_on_a_visible_object(
    unauthed_client: TestClient, web_context: Context, world: _World, relation: str
) -> None:
    seen = world.users[relation][0]
    headers = _headers(web_context, world.member_id, _MEMBER_PERMISSIONS)
    resp = unauthed_client.get("/actors/lookup", params={"id": seen}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "data": [{"id": seen, "actor_type": "user", "name": f"Rel {relation}-seen", "active": True}]
    }


@pytest.mark.parametrize("relation", _RELATIONS)
def test_relation_on_a_hidden_object_answers_like_an_unknown_id(
    unauthed_client: TestClient, web_context: Context, world: _World, relation: str
) -> None:
    hidden = world.users[relation][1]
    headers = _headers(web_context, world.member_id, _MEMBER_PERMISSIONS)
    unknown = _lookup(unauthed_client, headers, _UNKNOWN_USER)
    assert _lookup(unauthed_client, headers, hidden) == unknown
    assert unknown == b'{"data":[]}'


@pytest.mark.parametrize("relation", [r for r, gate in _GATE.items() if gate is not None])
def test_relation_without_its_route_permission_answers_like_an_unknown_id(
    unauthed_client: TestClient, web_context: Context, world: _World, relation: str
) -> None:
    seen = world.users[relation][0]
    gate = _GATE[relation]
    withheld = tuple(p for p in _MEMBER_PERMISSIONS if p != gate)
    headers = _headers(web_context, world.member_id, withheld)
    assert _lookup(unauthed_client, headers, seen) == _lookup(
        unauthed_client, headers, _UNKNOWN_USER
    )


def test_held_execution_decider_is_visible_without_any_permission(
    unauthed_client: TestClient, web_context: Context, world: _World
) -> None:
    """The holds routes take no permission, so neither does the decider relation."""
    headers = _headers(web_context, world.member_id, ())
    seen, hidden = world.users["decided_by"]
    assert b'"id":"' + seen.encode() + b'"' in _lookup(unauthed_client, headers, seen)
    assert _lookup(unauthed_client, headers, hidden) == _lookup(
        unauthed_client, headers, _UNKNOWN_USER
    )


def test_other_owner_resolves_its_own_relations_only(
    unauthed_client: TestClient, web_context: Context, world: _World
) -> None:
    """The hidden twins are visible to their own owner, and the member's are not."""
    headers = _headers(web_context, world.other_owner_id, _MEMBER_PERMISSIONS)
    ids = [
        u
        for rel in ("registered_by", "denied_by", "decided_by", "consented_by")
        for u in world.users[rel]
    ]
    resp = unauthed_client.get("/actors/lookup", params=[("id", i) for i in ids], headers=headers)
    assert resp.status_code == 200, resp.text
    expected = {
        world.users[rel][1] for rel in ("registered_by", "denied_by", "decided_by", "consented_by")
    }
    assert {e["id"] for e in resp.json()["data"]} == expected


def test_agent_resolves_the_decider_of_a_hold_it_filed(
    unauthed_client: TestClient, web_context: Context, world: _World
) -> None:
    """An agent sees the holds it filed itself, so it labels their decider."""
    headers = _headers(web_context, world.member_agent_id, (), agent_of=world.member_id)
    seen, hidden = world.users["decided_by"]
    resp = unauthed_client.get("/actors/lookup", params={"id": seen}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert [e["id"] for e in resp.json()["data"]] == [seen]
    # Another owner's agent filed the other hold: invisible, so like an unknown id.
    assert _lookup(unauthed_client, headers, hidden) == _lookup(
        unauthed_client, headers, _UNKNOWN_USER
    )
