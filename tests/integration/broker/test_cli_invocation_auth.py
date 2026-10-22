"""Integration tests: the broker resolves CLI invocation tokens only at its CLI facades.

Builds the real standalone broker app (so ``install_broker_auth`` wires both
validators exactly as production does) with one test-only route behind
``require_cli_facade_invocation("github")`` mounted through the container's
``extra_routers``. Tokens are minted through the real ``CliInvocationService``
against the integration admin DB, then presented to:

- the facade route, where only a live ``cit_`` token for ``github`` passes and
  every kill switch on the parent access token (revocation, expiry, agent or
  owner status, live permissions) also ends the child;
- the broker's forward-proxy catch-all (the ``/execute`` path), which must
  refuse a ``cit_`` token outright while it still accepts the parent ``at_``.

The token caches run with a zero TTL so each request re-resolves.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.cli_invocations import CliInvocation
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentRepository,
    UserRepository,
)
from jentic_one.auth.services.cli_invocation_service import CliInvocationService
from jentic_one.auth.services.schemas.cli_invocations import CliInvocationMintPayload
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.deps import require_cli_facade_invocation
from jentic_one.shared.auth.cli_invocation import CliInvocationIdentity
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import AppConfig, CliFacadesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.web.container import AppContainer

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_cli_broker_seed"
_OWNER_ID = "usr_cli_broker_owner"

_facade_router = APIRouter()
_RequireGithubInvocation = require_cli_facade_invocation("github")


@_facade_router.get("/github/whoami")
async def _github_facade_probe(
    invocation: CliInvocationIdentity = _RequireGithubInvocation,
) -> dict[str, object]:
    return {
        "sub": invocation.identity.sub,
        "permissions": invocation.identity.permissions,
        **invocation.claims.model_dump(mode="json"),
    }


@pytest.fixture()
def cli_ctx(integration_context: Context) -> Iterator[Context]:
    """Facades on for github and cloudflare; broker token caches off (restored after)."""
    config = integration_context.config
    prior_facades = config.cli_facades
    prior_ttl = config.broker.resolve_cache_ttl_seconds
    config.cli_facades = CliFacadesConfig.model_validate(
        {
            "enabled": True,
            "vendors": {
                "github": {"enabled": True, "host": "github.jentic.test"},
                "cloudflare": {"enabled": True},
            },
        }
    )
    config.broker.resolve_cache_ttl_seconds = 0.0
    yield integration_context
    config.cli_facades = prior_facades
    config.broker.resolve_cache_ttl_seconds = prior_ttl


@pytest.fixture()
async def clean_rows(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(CliInvocation))
            await session.execute(delete(AccessToken))
            await session.execute(delete(RefreshToken))
            await session.execute(delete(ActorPermissionGrant))
            await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
            await session.execute(delete(User).where(User.created_by == _SEED_MARKER))

    await _truncate()
    yield
    await _truncate()


@pytest.fixture()
def broker_app(cli_ctx: Context, integration_config: AppConfig) -> FastAPI:
    # The broker-shaped context shares the integration databases; a standalone
    # broker process is scoped exactly like this.
    container = AppContainer(
        ctx=cli_ctx, extra_routers=[(_facade_router, "/_test/cli-facade", ["test"])]
    )
    assert "admin" in _expand_allowed_dbs(["broker"], integration_config)
    return create_broker_app(cli_ctx, container=container)


async def _seed_agent(ctx: Context, *, owner_active: bool = True) -> str:
    async with ctx.admin_db.transaction() as session:
        await UserRepository.create(
            session,
            id=_OWNER_ID,
            email=f"{_OWNER_ID}@cli.test",
            first_name="Cli",
            last_name="Owner",
            active=owner_active,
            created_by=_SEED_MARKER,
        )
        agent = await AgentRepository.create(
            session,
            name="cli-invocation-agent",
            owner_id=_OWNER_ID,
            registered_by=_OWNER_ID,
            created_by=_SEED_MARKER,
            status=ActorStatus.ACTIVE,
        )
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent.id,
            actor_type=ActorType.AGENT,
            permission=BROKER_EXECUTE_PERMISSION,
            granted_by=_OWNER_ID,
            created_by=_SEED_MARKER,
        )
        return agent.id


async def _mint(ctx: Context, agent_id: str, *, vendor: str = "github") -> tuple[str, str, str]:
    """Returns (access_token, invocation_token, invocation_id)."""
    access, _refresh = await TokenService(ctx).issue_pair(
        agent_id, ActorType.AGENT, [BROKER_EXECUTE_PERMISSION]
    )
    minted = await CliInvocationService(ctx).mint(
        CliInvocationMintPayload(vendor=vendor, command_title="pr create"),
        identity=Identity(sub=agent_id, actor_type=ActorType.AGENT),
        parent_token=access,
    )
    return access, minted.token, minted.invocation.id


def _client(app: FastAPI, authorization: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": authorization},
    )


async def _facade_status(app: FastAPI, authorization: str) -> int:
    async with _client(app, authorization) as client:
        return (await client.get("/_test/cli-facade/github/whoami")).status_code


# ── where a cit_ token is accepted ───────────────────────────────────────────


async def test_facade_accepts_a_live_invocation_token_with_its_claims(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, invocation_id = await _mint(cli_ctx, agent_id)

    async with _client(broker_app, f"token {token}") as client:
        resp = await client.get("/_test/cli-facade/github/whoami")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sub"] == agent_id
    assert body["invocation_id"] == invocation_id
    assert body["vendor"] == "github"
    assert body["command_title"] == "pr create"
    assert body["credential_name"] is None
    assert len(body["trace_id"]) == 32
    assert BROKER_EXECUTE_PERMISSION in body["permissions"]


async def test_execute_path_refuses_a_cit_token_but_accepts_its_parent(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    """The fail-closed property: ``require_broker_identity`` never resolves ``cit_``."""
    agent_id = await _seed_agent(cli_ctx)
    access, token, _invocation_id = await _mint(cli_ctx, agent_id)

    async with _client(broker_app, f"Bearer {token}") as client:
        refused = await client.get("/api.example.com/v1/things")
    async with AsyncClient(
        transport=ASGITransport(app=broker_app),
        base_url="http://testserver",
        headers={"x-jentic-api-key": token},
    ) as client:
        refused_header = await client.get("/api.example.com/v1/things")
    # The app runs without its lifespan, so past authentication the proxy
    # fails on its unstarted runner registry; any answer but 401 shows the
    # parent authenticated where the child did not.
    async with AsyncClient(
        transport=ASGITransport(app=broker_app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {access}"},
    ) as client:
        accepted = await client.get("/api.example.com/v1/things")

    assert refused.status_code == 401
    assert refused.json()["detail"] == "Invalid or expired access token"
    assert refused_header.status_code == 401
    assert accepted.status_code != 401
    assert await InProcessTokenResolver(cli_ctx.admin_db).resolve_access_token(token) is None


async def test_facade_refuses_an_access_token(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    access, _token, _invocation_id = await _mint(cli_ctx, agent_id)
    assert await _facade_status(broker_app, f"token {access}") == 401
    assert await _facade_status(broker_app, f"Bearer {access}") == 401


async def test_facade_refuses_a_token_minted_for_another_vendor(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id, vendor="cloudflare")
    assert await _facade_status(broker_app, f"Bearer {token}") == 401


# ── kill switches ────────────────────────────────────────────────────────────


async def test_revoking_the_invocation_ends_it(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, invocation_id = await _mint(cli_ctx, agent_id)
    assert await _facade_status(broker_app, f"token {token}") == 200

    await CliInvocationService(cli_ctx).revoke(
        invocation_id, identity=Identity(sub=agent_id, actor_type=ActorType.AGENT)
    )
    assert await _facade_status(broker_app, f"token {token}") == 401


async def test_revoking_the_parent_access_token_ends_the_child(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id)
    assert await _facade_status(broker_app, f"token {token}") == 200

    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.actor_id == agent_id)
            .values(revoked_at=datetime.now(UTC))
        )
    assert await _facade_status(broker_app, f"token {token}") == 401


async def test_expired_parent_access_token_ends_the_child(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.actor_id == agent_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert await _facade_status(broker_app, f"token {token}") == 401


@pytest.mark.parametrize("status", [ActorStatus.DISABLED, ActorStatus.ARCHIVED])
async def test_inactive_agent_ends_the_child(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None, status: ActorStatus
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id)
    assert await _facade_status(broker_app, f"token {token}") == 200

    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(status=status))
    assert await _facade_status(broker_app, f"token {token}") == 401


async def test_inactive_owner_ends_the_child(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(update(User).where(User.id == _OWNER_ID).values(active=False))
    assert await _facade_status(broker_app, f"token {token}") == 401


async def test_losing_the_execute_permission_is_403_at_the_facade(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == agent_id)
        )
    assert await _facade_status(broker_app, f"token {token}") == 403


@pytest.mark.parametrize("column", ["expires_at", "hard_expires_at"])
async def test_expired_invocation_is_refused(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None, column: str
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, invocation_id = await _mint(cli_ctx, agent_id)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            update(CliInvocation)
            .where(CliInvocation.id == invocation_id)
            .values({column: datetime.now(UTC) - timedelta(seconds=1)})
        )
    assert await _facade_status(broker_app, f"token {token}") == 401


async def test_renewal_keeps_a_nearly_expired_invocation_working(
    broker_app: FastAPI, cli_ctx: Context, clean_rows: None
) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, invocation_id = await _mint(cli_ctx, agent_id)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            update(CliInvocation)
            .where(CliInvocation.id == invocation_id)
            .values(expires_at=datetime.now(UTC) + timedelta(seconds=2))
        )
    renewed = await CliInvocationService(cli_ctx).renew(
        invocation_id, identity=Identity(sub=agent_id, actor_type=ActorType.AGENT)
    )
    assert renewed.expires_at > datetime.now(UTC) + timedelta(minutes=5)
    assert await _facade_status(broker_app, f"token {token}") == 200


async def test_missing_parent_access_token_fails_closed(cli_ctx: Context, clean_rows: None) -> None:
    agent_id = await _seed_agent(cli_ctx)
    _access, token, _invocation_id = await _mint(cli_ctx, agent_id)
    resolver = InProcessTokenResolver(cli_ctx.admin_db)
    assert await resolver.resolve_cli_invocation_token(token) is not None

    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(delete(AccessToken).where(AccessToken.actor_id == agent_id))
    assert await resolver.resolve_cli_invocation_token(token) is None
