"""Integration tests: minting, renewing and revoking CLI invocation tokens on the auth surface.

Drives the real standalone auth app over ASGI against the integration admin DB.
Covers who may mint (only an agent presenting its own ``at_`` access token and
holding ``capabilities:execute``), the ``cli_facades`` gates, the per-agent
mint rate limit, renewal up to the hard cap, revocation, ownership of
renew/revoke, and that a ``cit_`` token authenticates nowhere on the control
plane.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.cli_invocations import CliInvocation
from jentic_one.admin.repos import AgentCredentialRepository
from jentic_one.auth.services.crypto import generate_agent_api_key, hash_secret
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.app import create_app as create_auth_app
from jentic_one.shared.auth.cli_invocation import hash_token
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import CliFacadesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.integration.auth.seeds import SEED_MARKER, seed_agent, seed_user

pytestmark = pytest.mark.integration

_OWNER_ID = "usr_cli_invocation_owner"
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _facades(**overrides: object) -> CliFacadesConfig:
    doc: dict[str, object] = {
        "enabled": True,
        "vendors": {
            "github": {"enabled": True, "host": "github.jentic.test"},
            "cloudflare": {"enabled": True},
        },
    }
    doc.update(overrides)
    return CliFacadesConfig.model_validate(doc)


@pytest.fixture()
def cli_ctx(integration_context: Context) -> Iterator[Context]:
    """The integration context with the github and cloudflare facades on (restored after)."""
    prior = integration_context.config.cli_facades
    integration_context.config.cli_facades = _facades()
    yield integration_context
    integration_context.config.cli_facades = prior


@pytest.fixture()
async def clean_cli_invocations(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(CliInvocation))

    await _truncate()
    yield
    await _truncate()


@pytest.fixture()
async def owner_id(integration_context: Context, clean_cli_invocations: None) -> str:
    return await seed_user(integration_context, _OWNER_ID)


@pytest.fixture()
def auth_app(cli_ctx: Context) -> FastAPI:
    return create_auth_app(cli_ctx)


def _client(app: FastAPI, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _agent_access_token(
    ctx: Context, owner_id: str, *, scopes: list[str] | None = None
) -> tuple[str, str]:
    granted = [BROKER_EXECUTE_PERMISSION] if scopes is None else scopes
    agent_id = await seed_agent(ctx, owner_id=owner_id, scopes=granted)
    access, _refresh = await TokenService(ctx).issue_pair(agent_id, ActorType.AGENT, granted)
    return agent_id, access


async def _mint(app: FastAPI, token: str, **body: object) -> dict[str, object]:
    async with _client(app, token) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "github", **body})
    assert resp.status_code == 201, resp.text
    payload: dict[str, object] = resp.json()
    return payload


async def _row(ctx: Context, invocation_id: object) -> CliInvocation:
    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(select(CliInvocation).where(CliInvocation.id == invocation_id))
        ).scalar_one()
        return row


# ── mint ─────────────────────────────────────────────────────────────────────


async def test_agent_mints_a_cit_token_bound_to_its_access_token(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    before = datetime.now(UTC)

    minted = await _mint(auth_app, access, command_title="pr create")

    token = str(minted["token"])
    assert token.startswith("cit_")
    assert minted["vendor"] == "github"
    assert minted["command_title"] == "pr create"
    assert minted["credential_name"] is None
    assert minted["revoked_at"] is None
    assert _TRACE_ID_RE.match(str(minted["trace_id"]))
    expires_at = datetime.fromisoformat(str(minted["expires_at"]))
    hard_expires_at = datetime.fromisoformat(str(minted["hard_expires_at"]))
    assert before + timedelta(seconds=590) < expires_at <= hard_expires_at
    assert hard_expires_at <= before + timedelta(seconds=3610)

    row = await _row(cli_ctx, minted["id"])
    assert row.actor_id == agent_id
    assert row.actor_type == "agent"
    assert row.token_hash == hash_token(token)
    assert row.parent_token_hash == hash_token(access)
    assert row.trace_id == minted["trace_id"]
    assert row.credential_name is None


async def test_mint_stores_the_requested_credential_name(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)

    minted = await _mint(auth_app, access, credential_name="GitHub (acme org)")

    assert minted["credential_name"] == "GitHub (acme org)"
    row = await _row(cli_ctx, minted["id"])
    assert row.credential_name == "GitHub (acme org)"


async def test_each_mint_gets_its_own_token_and_trace(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    first = await _mint(auth_app, access)
    second = await _mint(auth_app, access)
    assert first["token"] != second["token"]
    assert first["trace_id"] != second["trace_id"]


async def test_hard_cap_never_outlives_the_parent_access_token(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    # The default access-token TTL (1 h) equals the default hard cap, so a
    # shorter-lived parent is what shows the cap.
    agent_id = await seed_agent(cli_ctx, owner_id=owner_id, scopes=[BROKER_EXECUTE_PERMISSION])
    access = await TokenService(cli_ctx).issue_access_only(
        agent_id, ActorType.AGENT, [BROKER_EXECUTE_PERMISSION], ttl_seconds=120
    )
    minted = await _mint(auth_app, access)
    hard = datetime.fromisoformat(str(minted["hard_expires_at"]))
    assert hard <= datetime.now(UTC) + timedelta(seconds=120)
    assert datetime.fromisoformat(str(minted["expires_at"])) == hard


@pytest.mark.parametrize(
    "facades",
    [
        CliFacadesConfig(),  # master switch off
        _facades(enabled=False),
        _facades(vendors={"github": {"enabled": False}, "cloudflare": {"enabled": True}}),
    ],
)
async def test_mint_is_404_while_the_facade_is_off(
    cli_ctx: Context, owner_id: str, facades: CliFacadesConfig
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    cli_ctx.config.cli_facades = facades
    async with _client(create_auth_app(cli_ctx), access) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "github"})
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("cli_facade_not_found")


async def test_mint_for_unknown_vendor_is_404(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    async with _client(auth_app, access) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "gitlab"})
    assert resp.status_code == 404


async def test_user_cannot_mint(auth_app: FastAPI, cli_ctx: Context, owner_id: str) -> None:
    access, _refresh = await TokenService(cli_ctx).issue_pair(
        owner_id, ActorType.USER, [BROKER_EXECUTE_PERMISSION]
    )
    async with _client(auth_app, access) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "github"})
    assert resp.status_code == 403


async def test_agent_api_key_cannot_mint(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    agent_id = await seed_agent(cli_ctx, owner_id=owner_id, scopes=[BROKER_EXECUTE_PERMISSION])
    key = generate_agent_api_key()
    async with cli_ctx.admin_db.transaction() as session:
        await AgentCredentialRepository.set_api_key_hash(
            session, agent_id, api_key_hash=hash_secret(key), created_by=SEED_MARKER
        )
    async with _client(auth_app, key) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "github"})
    assert resp.status_code == 403
    assert resp.json()["type"].endswith("cli_invocation_mint_forbidden")


async def test_agent_without_execute_permission_cannot_mint(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id, scopes=["apis:read"])
    async with _client(auth_app, access) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "github"})
    assert resp.status_code == 403


async def test_a_cit_token_cannot_mint_or_authenticate_on_the_control_plane(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    token = str((await _mint(auth_app, access))["token"])
    async with _client(auth_app, token) as client:
        assert (await client.get("/me")).status_code == 401
        resp = await client.post("/cli-invocations", json={"vendor": "github"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"vendor": "github", "command_title": "pr create --title secret"},
        {"vendor": "github", "command_title": "x" * 129},
        {"vendor": "GitHub"},
        {"vendor": "github", "extra": 1},
    ],
)
async def test_mint_body_is_validated(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str, body: dict[str, object]
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    async with _client(auth_app, access) as client:
        resp = await client.post("/cli-invocations", json=body)
    assert resp.status_code == 422


async def test_minting_is_rate_limited_per_agent(cli_ctx: Context, owner_id: str) -> None:
    cli_ctx.config.cli_facades = _facades(mint_rate_limit_per_minute=2)
    app = create_auth_app(cli_ctx)
    _first_agent, first = await _agent_access_token(cli_ctx, owner_id)
    _second_agent, second = await _agent_access_token(cli_ctx, owner_id)
    await _mint(app, first)
    await _mint(app, first)
    async with _client(app, first) as client:
        resp = await client.post("/cli-invocations", json={"vendor": "github"})
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    # Another agent has its own bucket.
    await _mint(app, second)


# ── renew / revoke ───────────────────────────────────────────────────────────


async def test_renew_extends_expiry_but_never_past_the_hard_cap(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    minted = await _mint(auth_app, access)
    near = datetime.now(UTC) + timedelta(seconds=30)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            update(CliInvocation)
            .where(CliInvocation.id == minted["id"])
            .values(expires_at=datetime.now(UTC) + timedelta(seconds=5))
        )
    async with _client(auth_app, access) as client:
        renewed = await client.post(f"/cli-invocations/{minted['id']}:renew")
        assert renewed.status_code == 200
        assert datetime.fromisoformat(renewed.json()["expires_at"]) > near

        async with cli_ctx.admin_db.transaction() as session:
            await session.execute(
                update(CliInvocation)
                .where(CliInvocation.id == minted["id"])
                .values(hard_expires_at=near)
            )
        capped = await client.post(f"/cli-invocations/{minted['id']}:renew")
    assert capped.status_code == 200
    assert datetime.fromisoformat(capped.json()["expires_at"]) == near


async def test_expired_or_hard_capped_invocation_cannot_be_renewed(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    expired = await _mint(auth_app, access)
    capped = await _mint(auth_app, access)
    past = datetime.now(UTC) - timedelta(seconds=1)
    async with cli_ctx.admin_db.transaction() as session:
        await session.execute(
            update(CliInvocation).where(CliInvocation.id == expired["id"]).values(expires_at=past)
        )
        await session.execute(
            update(CliInvocation)
            .where(CliInvocation.id == capped["id"])
            .values(hard_expires_at=past)
        )
    async with _client(auth_app, access) as client:
        for invocation in (expired, capped):
            resp = await client.post(f"/cli-invocations/{invocation['id']}:renew")
            assert resp.status_code == 409
            assert resp.json()["type"].endswith("cli_invocation_not_renewable")


async def test_revoke_is_idempotent_and_ends_renewal(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    minted = await _mint(auth_app, access)
    async with _client(auth_app, access) as client:
        assert (await client.post(f"/cli-invocations/{minted['id']}:revoke")).status_code == 204
        assert (await client.post(f"/cli-invocations/{minted['id']}:revoke")).status_code == 204
        resp = await client.post(f"/cli-invocations/{minted['id']}:renew")
    assert resp.status_code == 409
    assert (await _row(cli_ctx, minted["id"])).revoked_at is not None


async def test_only_the_minting_agent_can_renew_or_revoke(
    auth_app: FastAPI, cli_ctx: Context, owner_id: str
) -> None:
    _agent_id, access = await _agent_access_token(cli_ctx, owner_id)
    _other_id, other_access = await _agent_access_token(cli_ctx, owner_id)
    minted = await _mint(auth_app, access)
    async with _client(auth_app, other_access) as client:
        assert (await client.post(f"/cli-invocations/{minted['id']}:renew")).status_code == 404
        assert (await client.post(f"/cli-invocations/{minted['id']}:revoke")).status_code == 404
    owner_access, _refresh = await TokenService(cli_ctx).issue_pair(
        owner_id, ActorType.USER, [BROKER_EXECUTE_PERMISSION]
    )
    async with _client(auth_app, owner_access) as client:
        assert (await client.post(f"/cli-invocations/{minted['id']}:revoke")).status_code == 403
    assert (await _row(cli_ctx, minted["id"])).revoked_at is None
