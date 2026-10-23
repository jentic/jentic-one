"""Integration tests: ``GET /agents/{id}`` reads on the effective permission set.

An agent API key resolves to the agent's direct grants, unexpanded.
``AgentService.get_readable_agent`` (and ``list_credentials``, which shares its
check) expands implied permissions the same way the route gate does, so a key
holding ``agents:write`` reads a sibling agent like one holding ``agents:read``.

Router, service, token verifier, repositories and DB are all real: each request
presents a ``jak_`` key that the platform verifier resolves from
``agent_credentials`` and ``actor_permission_grants``.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from jentic_one.admin.repos import AgentCredentialRepository
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.auth.web.verifier import make_superset_verifier
from jentic_one.shared.auth.api_key_resolver import AGENT_API_KEY_PREFIX
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    AGENTS_WRITE,
    OWNER_AGENTS_READ,
)
from jentic_one.shared.context import Context
from jentic_one.shared.web.auth import API_KEY_HEADER
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

OWNER = "usr_read_perm_owner"


@asynccontextmanager
async def _client(ctx: Context) -> AsyncIterator[AsyncClient]:
    """The agents router behind the platform token verifier (no identity override)."""
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.verify_token = make_superset_verifier(ctx)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _issue_api_key(ctx: Context, agent_id: str) -> str:
    """Store a fresh ``jak_`` key's digest on ``agent_id`` and return the plaintext."""
    raw_key = f"{AGENT_API_KEY_PREFIX}{secrets.token_hex(16)}"
    async with ctx.admin_db.transaction() as session:
        await AgentCredentialRepository.set_api_key_hash(
            session,
            agent_id,
            api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            created_by=OWNER,
        )
    return raw_key


@pytest.mark.parametrize(
    ("permissions", "readable"),
    [
        ([], False),
        ([AGENTS_READ], True),
        ([AGENTS_WRITE], True),
    ],
    ids=["no-agents-permission", "agents-read", "agents-write-implies-read"],
)
async def test_api_key_reads_sibling_agent_on_effective_permissions(
    integration_context: Context,
    clean_grants: None,
    permissions: list[str],
    readable: bool,
) -> None:
    """A sibling agent's key reads the target with ``agents:read`` or ``agents:write``.

    Without either, both reads answer the same 404 as an id that does not
    exist, so the response does not reveal the agent.
    """
    ctx = integration_context
    owner_id = await seeds.seed_user(ctx, OWNER)
    target_id = await seeds.seed_agent(ctx, owner_id=owner_id, scopes=[], name="target")
    sibling_id = await seeds.seed_agent(
        ctx, owner_id=owner_id, scopes=[OWNER_AGENTS_READ, *permissions], name="sibling"
    )
    headers = {API_KEY_HEADER: await _issue_api_key(ctx, sibling_id)}

    async with _client(ctx) as client:
        agent_resp = await client.get(f"/agents/{target_id}", headers=headers)
        bindings_resp = await client.get(f"/agents/{target_id}/credentials", headers=headers)
        missing_resp = await client.get("/agents/agnt_missing", headers=headers)

    if readable:
        assert agent_resp.status_code == 200, agent_resp.text
        assert agent_resp.json()["id"] == target_id
        assert bindings_resp.status_code == 200, bindings_resp.text
        assert bindings_resp.json() == {"data": []}
    else:
        assert agent_resp.status_code == 404, agent_resp.text
        assert bindings_resp.status_code == 404, bindings_resp.text
    assert missing_resp.status_code == 404, missing_resp.text
