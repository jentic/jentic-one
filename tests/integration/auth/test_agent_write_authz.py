"""Integration tests: permission ceiling and owner scoping on agent write routes.

- ``POST /agents`` and ``PUT /agents/{id}/permissions`` enforce the agent permission
  ceiling: a caller without ``org:admin`` may grant only permissions it holds (or
  the default agent baseline), never ``org:admin`` / ``agents:write``, and no
  permission outside the permission catalogue.
- ``PUT …/permissions``, ``PATCH``, ``:disable``, ``:enable`` and ``DELETE`` are
  owner-or-``org:admin`` with a uniform 404; an ``owner_id`` change is
  ``org:admin``-only.
- ``:approve`` / ``:deny`` are owner-or-``org:admin`` with the same uniform
  404; an unowned (unclaimed self-registered) agent is decided only by an
  ``org:admin``, who becomes its owner on approval. Approving applies the
  approver's ceiling to the permissions a self-registration requested (they
  become live on approval).

Router, service, repositories and DB are real; the only shim is the identity
dependency override (the same pattern as ``test_oauth_grant_transfer.py``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import ActorPermissionGrantRepository, AgentRepository, AuditRepository
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.services.registration_service import RegistrationService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.shared.audit import AuditAction, AuditTargetType
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.web.deps import resolve_identity
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_SQLITE = os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite"

OWNER = "usr_authz_owner"
OTHER = "usr_authz_other"
ADMIN = "usr_authz_admin"
_WRITER_PERMS = ["agents:read", "agents:write"]
_DCR_JWKS: dict[str, Any] = {
    "keys": [{"kty": "OKP", "crv": "Ed25519", "x": "dGVzdC1wdWJsaWMta2V5LWJhc2U2NA", "kid": "k1"}]
}


@pytest.fixture()
async def users(integration_context: Context, clean_grants: None) -> AsyncGenerator[None, None]:
    """Seed the three callers; drop every agent they own before ``clean_grants`` teardown.

    Agents created through ``POST /agents`` are stamped ``created_by`` with the
    caller, not the seed marker, so ``clean_grants`` would not remove them and
    the owner FK would block the seeded users' deletion.
    """
    for user_id in (OWNER, OTHER, ADMIN):
        await seeds.seed_user(integration_context, user_id)
    yield
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.owner_id.in_([OWNER, OTHER, ADMIN])))
        await session.commit()


@pytest.fixture()
async def self_register(
    integration_context: Context, users: None
) -> AsyncGenerator[Callable[[str], Awaitable[str]], None]:
    """Self-register a pending agent through the real DCR service; delete it after."""
    created: list[str] = []

    async def _register(scope: str) -> str:
        result = await RegistrationService(integration_context).register(
            "self-registered", _DCR_JWKS, scope=scope
        )
        created.append(result.client_id)
        return result.client_id

    yield _register
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id.in_(created)))
        await session.commit()


def _writer(sub: str) -> Identity:
    return Identity(sub=sub, email=f"{sub}@authz.test", permissions=list(_WRITER_PERMS))


def _admin() -> Identity:
    return Identity(sub=ADMIN, email=f"{ADMIN}@authz.test", permissions=["org:admin"])


@asynccontextmanager
async def _client(ctx: Context, identity: Identity) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.dependency_overrides[resolve_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _create_owned_agent(ctx: Context) -> str:
    async with _client(ctx, _writer(OWNER)) as client:
        resp = await client.post("/agents", json={"name": "owned-agent"})
    assert resp.status_code == 201, resp.text
    agent_id: str = resp.json()["id"]
    return agent_id


async def _permissions(ctx: Context, agent_id: str) -> set[str]:
    async with ctx.admin_db.session() as session:
        grants = await ActorPermissionGrantRepository.list_for_actor(session, agent_id)
    return {g.permission for g in grants}


async def _agent(ctx: Context, agent_id: str) -> Agent:
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
    assert agent is not None
    return agent


async def _audit_permissions(ctx: Context, agent_id: str, action: AuditAction) -> object:
    async with ctx.admin_db.session() as session:
        entries = await AuditRepository.list_by_target(session, AuditTargetType.AGENT, agent_id)
    matching = [e for e in entries if e.action == action]
    assert len(matching) == 1
    assert matching[0].after is not None
    return matching[0].after.get("permissions")


async def _register_audit_permissions(ctx: Context, agent_id: str) -> object:
    return await _audit_permissions(ctx, agent_id, AuditAction.REGISTER)


async def _claim_as(ctx: Context, agent_id: str, owner_id: str) -> None:
    """Give a self-registered agent an owner, as a successful ``:claim`` does."""
    async with ctx.admin_db.transaction() as session:
        agent = await AgentRepository.get_by_id_for_update(session, agent_id)
        assert agent is not None
        await AgentRepository.set_owner_from_claim(session, agent, owner_id=owner_id)


# ---------------------------------------------------------------------------
# Permission ceiling — POST /agents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("permission", ["org:admin", "agents:write", "users:write"])
async def test_create_rejects_permission_above_caller_ceiling(
    integration_context: Context, users: None, permission: str
) -> None:
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post("/agents", json={"name": "escalate", "permissions": [permission]})
    assert resp.status_code == 403
    assert resp.json()["type"] == "agent_permission_not_grantable"


async def test_create_rejects_unknown_permission(integration_context: Context, users: None) -> None:
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post("/agents", json={"name": "bogus", "permissions": ["made:up"]})
    assert resp.status_code == 422
    assert resp.json()["type"] == "unknown_agent_permission"


async def test_create_without_permissions_grants_defaults_and_audits_them(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)
    assert await _register_audit_permissions(integration_context, agent_id) == list(
        DEFAULT_AGENT_PERMISSIONS
    )


async def test_create_with_held_or_baseline_permissions_succeeds(
    integration_context: Context, users: None
) -> None:
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post(
            "/agents", json={"name": "narrow", "permissions": ["agents:read", "capabilities:read"]}
        )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    assert await _permissions(integration_context, agent_id) == {"agents:read", "capabilities:read"}
    assert await _register_audit_permissions(integration_context, agent_id) == [
        "agents:read",
        "capabilities:read",
    ]


async def test_admin_create_may_grant_org_admin(integration_context: Context, users: None) -> None:
    async with _client(integration_context, _admin()) as client:
        resp = await client.post(
            "/agents", json={"name": "admin-agent", "permissions": ["org:admin"]}
        )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    assert await _permissions(integration_context, agent_id) == {"org:admin"}
    assert await _register_audit_permissions(integration_context, agent_id) == ["org:admin"]


# ---------------------------------------------------------------------------
# Permission ceiling + owner scoping — PUT /agents/{id}/permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("permission", ["org:admin", "agents:write", "users:write"])
async def test_replace_permissions_rejects_permission_above_caller_ceiling(
    integration_context: Context, users: None, permission: str
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions", json={"permissions": [permission]}
        )
    assert resp.status_code == 403
    assert resp.json()["type"] == "agent_permission_not_grantable"
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)


async def test_replace_permissions_rejects_unknown_permission(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions", json={"permissions": ["made:up"]}
        )
    assert resp.status_code == 422
    assert resp.json()["type"] == "unknown_agent_permission"


async def test_owner_can_narrow_permissions(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions", json={"permissions": ["capabilities:read"]}
        )
    assert resp.status_code == 200, resp.text
    assert await _permissions(integration_context, agent_id) == {"capabilities:read"}


async def test_owner_may_keep_permission_an_admin_granted(
    integration_context: Context, users: None
) -> None:
    agent_id = await seeds.seed_agent(
        integration_context,
        owner_id=OWNER,
        scopes=["org:admin", "capabilities:read"],
    )
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions", json={"permissions": ["org:admin"]}
        )
    assert resp.status_code == 200, resp.text
    assert await _permissions(integration_context, agent_id) == {"org:admin"}


@pytest.mark.parametrize(
    "permission",
    ["org:admin", "agents:write", "users:write", "credentials:write"],
)
async def test_admin_replace_permissions_rejects_permission_above_owner_ceiling(
    integration_context: Context, users: None, permission: str
) -> None:
    await seeds.seed_permissions(integration_context, OWNER, list(_WRITER_PERMS))
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _admin()) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["capabilities:read", permission]},
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "agent_permission_exceeds_owner"
    assert permission in resp.json()["detail"]
    assert "owner does not hold it" in resp.json()["detail"]
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)


async def test_admin_replace_permissions_succeeds_for_org_admin_owner(
    integration_context: Context, users: None
) -> None:
    await seeds.seed_permissions(integration_context, ADMIN, ["org:admin"])
    async with _client(integration_context, _admin()) as client:
        created = await client.post("/agents", json={"name": "admin-owned"})
        assert created.status_code == 201, created.text
        agent_id = created.json()["id"]
        resp = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["org:admin", "agents:write", "users:write", "capabilities:read"]},
        )
    assert resp.status_code == 200, resp.text
    assert await _permissions(integration_context, agent_id) == {
        "org:admin",
        "agents:write",
        "users:write",
        "capabilities:read",
    }


async def test_admin_replace_permissions_preserves_already_held_and_rejects_new_over_owner(
    integration_context: Context, users: None
) -> None:
    await seeds.seed_permissions(integration_context, OWNER, ["agents:write"])
    agent_id = await seeds.seed_agent(
        integration_context,
        owner_id=OWNER,
        scopes=["org:admin", "capabilities:read"],
    )
    async with _client(integration_context, _admin()) as client:
        keep_resp = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["org:admin", "capabilities:read", "agents:read"]},
        )
        assert keep_resp.status_code == 200, keep_resp.text
        assert await _permissions(integration_context, agent_id) == {
            "org:admin",
            "capabilities:read",
            "agents:read",
        }

        reject_resp = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["org:admin", "users:write"]},
        )
    assert reject_resp.status_code == 409, reject_resp.text
    assert reject_resp.json()["type"] == "agent_permission_exceeds_owner"
    assert await _permissions(integration_context, agent_id) == {
        "org:admin",
        "capabilities:read",
        "agents:read",
    }


async def test_admin_replace_permissions_skips_retired_permission_in_owner_check(
    integration_context: Context, users: None
) -> None:
    await seeds.seed_permissions(integration_context, OWNER, [])
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _admin()) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["capabilities:read", "toolkits:read"]},
        )
    assert resp.status_code == 200, resp.text
    assert await _permissions(integration_context, agent_id) == {
        "capabilities:read",
        "toolkits:read",
    }


async def test_accepted_owner_grant_resolves_across_token_and_api_key_resolvers(
    integration_context: Context, users: None
) -> None:
    await seeds.seed_permissions(integration_context, OWNER, ["users:write", "agents:write"])
    agent_id = await _create_owned_agent(integration_context)
    granted = ["capabilities:execute", "users:write", "users:read"]
    async with _client(integration_context, _admin()) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": granted},
        )
    assert resp.status_code == 200, resp.text

    api_key = await AgentAuthService(integration_context).register_api_key(
        agent_id, identity=_admin()
    )
    access_token, _ = await TokenService(integration_context).issue_pair(
        agent_id, ActorType.AGENT, granted
    )

    key_resolved = await ApiKeyResolver(integration_context.admin_db).resolve(api_key)
    assert key_resolved is not None and key_resolved.active is True
    assert set(key_resolved.permissions) == set(granted)

    for resolver in (
        TokenService(integration_context),
        InProcessTokenResolver(integration_context.admin_db),
    ):
        tok_resolved = await resolver.resolve_access_token(access_token)
        assert tok_resolved is not None and tok_resolved.active is True
        assert set(tok_resolved.permissions) == set(granted)


async def test_replace_permissions_after_transfer_enforces_new_owner_ceiling(
    integration_context: Context, users: None
) -> None:
    await seeds.seed_permissions(integration_context, OWNER, ["users:write", "agents:write"])
    await seeds.seed_permissions(integration_context, OTHER, ["agents:read"])
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _admin()) as client:
        first_put = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["capabilities:read", "users:write"]},
        )
        assert first_put.status_code == 200, first_put.text

        transfer = await client.patch(f"/agents/{agent_id}", json={"owner_id": OTHER})
        assert transfer.status_code == 200, transfer.text
        assert await _permissions(integration_context, agent_id) == {"capabilities:read"}

        second_put = await client.put(
            f"/agents/{agent_id}/permissions",
            json={"permissions": ["capabilities:read", "users:write"]},
        )
    assert second_put.status_code == 409, second_put.text
    assert second_put.json()["type"] == "agent_permission_exceeds_owner"
    assert await _permissions(integration_context, agent_id) == {"capabilities:read"}


@pytest.mark.skipif(_SQLITE, reason="row locks require Postgres")
async def test_replace_permissions_waits_for_transfer_and_enforces_new_owner_ceiling(
    integration_context: Context, users: None
) -> None:
    """An in-flight ownership transfer holds the agent row lock so a concurrent
    ``replace_permissions`` waits and then evaluates the new owner's ceiling."""
    await seeds.seed_permissions(integration_context, OWNER, ["users:write", "agents:write"])
    await seeds.seed_permissions(integration_context, OTHER, ["agents:read"])
    agent_id = await _create_owned_agent(integration_context)

    async with _client(integration_context, _admin()) as client:
        async with integration_context.admin_db.transaction() as session:
            locked = await AgentRepository.get_by_id_for_update(session, agent_id)
            assert locked is not None
            await AgentRepository.update_agent(session, agent_id, owner_id=OTHER)

            put_task = asyncio.create_task(
                client.put(
                    f"/agents/{agent_id}/permissions",
                    json={"permissions": ["capabilities:read", "users:write"]},
                )
            )
            await asyncio.sleep(0.3)
            assert not put_task.done(), "replace_permissions must wait on the agent row lock"

        resp = await put_task

    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "agent_permission_exceeds_owner"
    assert (await _agent(integration_context, agent_id)).owner_id == OTHER
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)


async def test_non_owner_cannot_replace_permissions(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions", json={"permissions": ["capabilities:read"]}
        )
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)


# ---------------------------------------------------------------------------
# Owner scoping — PATCH / disable / enable / archive
# ---------------------------------------------------------------------------


async def test_non_owner_cannot_patch_or_take_over(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OTHER)) as client:
        for body in ({"name": "renamed"}, {"owner_id": OTHER}):
            resp = await client.patch(f"/agents/{agent_id}", json=body)
            assert resp.status_code == 404, body
            assert resp.json()["type"] == "actor_not_found"
    agent = await _agent(integration_context, agent_id)
    assert agent.owner_id == OWNER
    assert agent.name == "owned-agent"


async def test_owner_transfer_is_admin_only(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.patch(f"/agents/{agent_id}", json={"owner_id": OTHER})
        assert resp.status_code == 403
        assert resp.json()["type"] == "owner_transfer_forbidden"
        assert (await _agent(integration_context, agent_id)).owner_id == OWNER

        # Re-sending the current owner is not a transfer.
        resp = await client.patch(
            f"/agents/{agent_id}", json={"name": "renamed", "owner_id": OWNER}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "renamed"

    async with _client(integration_context, _admin()) as client:
        resp = await client.patch(f"/agents/{agent_id}", json={"owner_id": OTHER})
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_id"] == OTHER


async def test_non_owner_cannot_disable_enable_or_archive(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OTHER)) as other:
        assert (await other.post(f"/agents/{agent_id}:disable")).status_code == 404
        assert (await other.delete(f"/agents/{agent_id}")).status_code == 404
        async with _client(integration_context, _writer(OWNER)) as owner:
            assert (await owner.post(f"/agents/{agent_id}:disable")).status_code == 204
        resp = await other.post(f"/agents/{agent_id}:enable")
        assert resp.status_code == 404
        assert resp.json()["type"] == "actor_not_found"

    assert (await _agent(integration_context, agent_id)).status == ActorStatus.DISABLED
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)


async def test_owner_can_disable_enable_and_archive(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        assert (await client.post(f"/agents/{agent_id}:disable")).status_code == 204
        assert (await client.post(f"/agents/{agent_id}:enable")).status_code == 204
        assert (await client.delete(f"/agents/{agent_id}")).status_code == 204
    assert (await _agent(integration_context, agent_id)).status == ActorStatus.ARCHIVED


async def test_admin_can_mutate_any_agent(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _admin()) as client:
        resp = await client.put(
            f"/agents/{agent_id}/permissions", json={"permissions": ["apis:read"]}
        )
        assert resp.status_code == 200, resp.text
        assert (await client.patch(f"/agents/{agent_id}", json={"name": "x"})).status_code == 200
        assert (await client.post(f"/agents/{agent_id}:disable")).status_code == 204
        assert (await client.post(f"/agents/{agent_id}:enable")).status_code == 204
        assert (await client.delete(f"/agents/{agent_id}")).status_code == 204


async def test_unknown_agent_is_404_for_owner_scoped_verbs(
    integration_context: Context, users: None
) -> None:
    missing = "agnt_does_not_exist"
    async with _client(integration_context, _writer(OWNER)) as client:
        responses = [
            await client.put(f"/agents/{missing}/permissions", json={"permissions": []}),
            await client.patch(f"/agents/{missing}", json={"name": "x"}),
            await client.post(f"/agents/{missing}:disable"),
            await client.post(f"/agents/{missing}:enable"),
            await client.delete(f"/agents/{missing}"),
            await client.post(f"/agents/{missing}:approve"),
            await client.post(f"/agents/{missing}:deny", json={"reason": "no"}),
        ]
    for resp in responses:
        assert resp.status_code == 404
        assert resp.json()["type"] == "actor_not_found"


# ---------------------------------------------------------------------------
# Registration decisions — owner or org:admin; unowned agents org:admin only
# ---------------------------------------------------------------------------


async def test_owner_can_approve_own_pending_agent(
    integration_context: Context, users: None
) -> None:
    agent_id = await seeds.seed_agent(
        integration_context, owner_id=OWNER, scopes=[], status=ActorStatus.PENDING
    )
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    agent = await _agent(integration_context, agent_id)
    assert agent.owner_id == OWNER
    assert agent.approved_by == OWNER


async def test_owner_can_deny_own_pending_agent(integration_context: Context, users: None) -> None:
    agent_id = await seeds.seed_agent(
        integration_context, owner_id=OWNER, scopes=[], status=ActorStatus.PENDING
    )
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post(f"/agents/{agent_id}:deny", json={"reason": "no"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"


@pytest.mark.parametrize("status", [ActorStatus.PENDING, ActorStatus.ACTIVE])
async def test_non_owner_cannot_approve_or_deny(
    integration_context: Context, users: None, status: ActorStatus
) -> None:
    # The 404 does not depend on the agent's status: a non-pending agent must
    # not answer 409 and so confirm it exists.
    agent_id = await seeds.seed_agent(integration_context, owner_id=OWNER, scopes=[], status=status)
    async with _client(integration_context, _writer(OTHER)) as client:
        responses = [
            await client.post(f"/agents/{agent_id}:approve"),
            await client.post(f"/agents/{agent_id}:deny", json={"reason": "no"}),
        ]
    for resp in responses:
        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "actor_not_found"
    assert (await _agent(integration_context, agent_id)).status == status


async def test_owner_deciding_non_pending_agent_is_409(
    integration_context: Context, users: None
) -> None:
    agent_id = await seeds.seed_agent(integration_context, owner_id=OWNER, scopes=[])
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 409, resp.text


async def test_admin_can_decide_any_owned_agent(integration_context: Context, users: None) -> None:
    approve_id = await seeds.seed_agent(
        integration_context, owner_id=OWNER, scopes=[], status=ActorStatus.PENDING
    )
    deny_id = await seeds.seed_agent(
        integration_context, owner_id=OWNER, scopes=[], status=ActorStatus.PENDING
    )
    async with _client(integration_context, _admin()) as client:
        approved = await client.post(f"/agents/{approve_id}:approve")
        denied = await client.post(f"/agents/{deny_id}:deny", json={"reason": "no"})
    assert approved.status_code == 200, approved.text
    assert denied.status_code == 200, denied.text
    # Approving someone else's agent leaves it with its owner.
    assert (await _agent(integration_context, approve_id)).owner_id == OWNER


async def test_non_admin_cannot_decide_unowned_agent(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    agent_id = await self_register("")
    async with _client(integration_context, _writer(OTHER)) as client:
        responses = [
            await client.post(f"/agents/{agent_id}:approve"),
            await client.post(f"/agents/{agent_id}:deny", json={"reason": "no"}),
        ]
    for resp in responses:
        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "actor_not_found"
    agent = await _agent(integration_context, agent_id)
    assert agent.status == ActorStatus.PENDING
    assert agent.owner_id is None


async def test_admin_approving_unowned_agent_becomes_owner(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    await seeds.seed_permissions(integration_context, ADMIN, ["org:admin"])
    agent_id = await self_register("users:write capabilities:read")
    async with _client(integration_context, _admin()) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert (await _agent(integration_context, agent_id)).owner_id == ADMIN
    assert await _permissions(integration_context, agent_id) == {"users:write", "capabilities:read"}


async def test_admin_approving_unowned_agent_accepts_admin_and_over_member_grants(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    """When an org:admin approves an unclaimed agent, the admin becomes owner and
    both self-requested over-member grants and pre-staged admin-authored grants
    are accepted under the admin owner's ceiling."""
    await seeds.seed_permissions(integration_context, ADMIN, ["org:admin"])
    agent_id = await self_register("users:write credentials:write capabilities:read")
    async with integration_context.admin_db.transaction() as session:
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent_id,
            actor_type=ActorType.AGENT,
            permission="agents:write",
            granted_by=ADMIN,
            created_by=ADMIN,
        )
    async with _client(integration_context, _admin()) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert (await _agent(integration_context, agent_id)).owner_id == ADMIN
    assert await _permissions(integration_context, agent_id) == {
        "agents:write",
        "users:write",
        "credentials:write",
        "capabilities:read",
    }


async def test_admin_can_deny_unowned_agent(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    agent_id = await self_register("")
    async with _client(integration_context, _admin()) as client:
        resp = await client.post(f"/agents/{agent_id}:deny", json={"reason": "no"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    assert (await _agent(integration_context, agent_id)).owner_id is None


async def test_claimant_can_approve_claimed_agent(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    agent_id = await self_register("")
    await _claim_as(integration_context, agent_id, OTHER)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 404, resp.text
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    agent = await _agent(integration_context, agent_id)
    assert agent.owner_id == OTHER
    assert agent.approved_by == OTHER


@pytest.mark.parametrize("requested", ["users:write", "credentials:write"])
async def test_approve_drops_self_requested_permission_above_owner_ceiling(
    integration_context: Context,
    self_register: Callable[[str], Awaitable[str]],
    requested: str,
) -> None:
    await seeds.seed_permissions(integration_context, OTHER, list(_WRITER_PERMS))
    agent_id = await self_register(f"capabilities:read {requested}")
    assert await _register_audit_permissions(integration_context, agent_id) == [
        "capabilities:read",
        requested,
    ]
    await _claim_as(integration_context, agent_id, OTHER)
    async with _client(integration_context, _admin()) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert await _permissions(integration_context, agent_id) == {"capabilities:read"}

    async with integration_context.admin_db.session() as session:
        entries = await AuditRepository.list_by_target(session, AuditTargetType.AGENT, agent_id)
    revoke_entries = [
        e
        for e in entries
        if e.action == AuditAction.REVOKE
        and e.reason == "self_requested_permission_above_owner_dropped"
    ]
    assert len(revoke_entries) == 1
    assert revoke_entries[0].before is not None
    assert set(revoke_entries[0].before["permissions"]) == {"capabilities:read", requested}
    assert revoke_entries[0].after == {"permissions": ["capabilities:read"]}
    assert await _audit_permissions(integration_context, agent_id, AuditAction.APPROVE) == [
        "capabilities:read"
    ]


@pytest.mark.parametrize("permission", ["org:admin", "agents:write", "users:write"])
async def test_approve_rejects_authored_grant_above_owner_ceiling(
    integration_context: Context,
    users: None,
    permission: str,
) -> None:
    await seeds.seed_permissions(integration_context, OTHER, list(_WRITER_PERMS))
    agent_id = await seeds.seed_agent(
        integration_context,
        owner_id=OTHER,
        scopes=["capabilities:read", permission],
        status=ActorStatus.PENDING,
    )
    async with _client(integration_context, _admin()) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "agent_permission_exceeds_owner"
    assert (await _agent(integration_context, agent_id)).status == ActorStatus.PENDING
    assert await _permissions(integration_context, agent_id) == {"capabilities:read", permission}


async def test_non_admin_owner_cannot_approve_authored_grant_above_caller_ceiling(
    integration_context: Context,
    users: None,
) -> None:
    await seeds.seed_permissions(integration_context, OTHER, ["users:write", *list(_WRITER_PERMS)])
    agent_id = await seeds.seed_agent(
        integration_context,
        owner_id=OTHER,
        scopes=["capabilities:read", "users:write"],
        status=ActorStatus.PENDING,
    )
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "agent_permission_not_grantable"
    assert (await _agent(integration_context, agent_id)).status == ActorStatus.PENDING


async def test_non_admin_owner_can_activate_requested_default_permissions(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    await seeds.seed_permissions(integration_context, OTHER, list(_WRITER_PERMS))
    # Unknown requested strings are dropped during registration and do not block the decision.
    agent_id = await self_register("capabilities:execute agents:read not-a:scope")
    await _claim_as(integration_context, agent_id, OTHER)
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    expected = ["agents:read", "capabilities:execute"]
    assert await _permissions(integration_context, agent_id) == set(expected)
    approve_permissions = await _audit_permissions(
        integration_context, agent_id, AuditAction.APPROVE
    )
    assert isinstance(approve_permissions, list)
    assert sorted(approve_permissions) == expected


async def test_approve_without_requested_permissions_grants_and_audits_defaults(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    agent_id = await self_register("")
    await _claim_as(integration_context, agent_id, OTHER)
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert await _permissions(integration_context, agent_id) == set(DEFAULT_AGENT_PERMISSIONS)
    assert await _audit_permissions(integration_context, agent_id, AuditAction.APPROVE) == list(
        DEFAULT_AGENT_PERMISSIONS
    )


# ---------------------------------------------------------------------------
# API key writes — owner or org:admin checked before active-status check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["generate-api-key", "revoke-api-key"])
@pytest.mark.parametrize(
    "status",
    [
        ActorStatus.ACTIVE,
        ActorStatus.DISABLED,
        ActorStatus.ARCHIVED,
        ActorStatus.PENDING,
    ],
)
async def test_non_owner_cannot_write_api_key_regardless_of_status(
    integration_context: Context, users: None, status: ActorStatus, verb: str
) -> None:
    """A non-owner gets 404 actor_not_found, never 409 invalid_transition, on foreign agents."""
    agent_id = await seeds.seed_agent(integration_context, owner_id=OWNER, scopes=[], status=status)
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:{verb}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["type"] == "actor_not_found"
    assert (await _agent(integration_context, agent_id)).status == status


@pytest.mark.parametrize("verb", ["generate-api-key", "revoke-api-key"])
async def test_non_admin_cannot_write_api_key_on_unowned_pending_agent(
    integration_context: Context,
    self_register: Callable[[str], Awaitable[str]],
    verb: str,
) -> None:
    """An unclaimed pending agent answers 404 actor_not_found to a non-admin caller."""
    agent_id = await self_register("")
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:{verb}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["type"] == "actor_not_found"
    agent = await _agent(integration_context, agent_id)
    assert agent.status == ActorStatus.PENDING
    assert agent.owner_id is None


@pytest.mark.parametrize("verb", ["generate-api-key", "revoke-api-key"])
@pytest.mark.parametrize(
    "status",
    [
        ActorStatus.DISABLED,
        ActorStatus.ARCHIVED,
        ActorStatus.PENDING,
    ],
)
@pytest.mark.parametrize("as_admin", [False, True], ids=["owner", "admin"])
async def test_owner_or_admin_key_write_on_non_active_agent_is_409(
    integration_context: Context,
    users: None,
    status: ActorStatus,
    verb: str,
    as_admin: bool,
) -> None:
    """The owner or org:admin still gets 409 invalid_transition on a non-active agent."""
    agent_id = await seeds.seed_agent(integration_context, owner_id=OWNER, scopes=[], status=status)
    caller = _admin() if as_admin else _writer(OWNER)
    async with _client(integration_context, caller) as client:
        resp = await client.post(f"/agents/{agent_id}:{verb}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "invalid_transition"
