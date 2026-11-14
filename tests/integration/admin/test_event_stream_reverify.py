"""Integration tests: an open ``/events/stream`` follows its caller's live state (#1836).

An agent API key resolves its permissions from ``actor_permission_grants`` on
every verification, so it is the credential that exercises live permission
changes end to end. Requests go through the combined app the composition root
serves, over the real ASGI stack. The recheck interval is set through config
(``admin.events.stream_recheck_seconds``) and the stream polls fast, so the
next check after a change is a short wait.

* Revoking ``events:read`` ends the open stream with ``stream_closed: forbidden``.
* Disabling the agent ends it with ``stream_closed: refused``.
* Revoking the agent's ``at_`` access token ends it with ``stream_closed: refused``.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text

from jentic_one.__main__ import _build_app
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.schemas.auth import ChangePasswordPayload
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.harness.event_stream import StreamProbe

pytestmark = pytest.mark.integration

_OWNER = "usr_streamrv_owner"
_AGENT = "agnt_streamrv_agent"
_FULL = ["registry", "admin", "control", "auth"]


@pytest.fixture()
async def agent_key(integration_context: Context) -> AsyncGenerator[str, None]:
    """An active, owned agent holding ``events:read`` and a ``jak_`` key."""
    db = integration_context.admin_db
    plaintext = f"jak_{secrets.token_hex(16)}"

    async def _cleanup() -> None:
        async with db.session() as session:
            for stmt in (
                "DELETE FROM actor_permission_grants WHERE actor_id = :agent",
                "DELETE FROM agent_credentials WHERE agent_id = :agent",
                "DELETE FROM access_tokens WHERE actor_id = :agent",
                "DELETE FROM refresh_tokens WHERE actor_id = :agent",
                "DELETE FROM agents WHERE id = :agent",
                "DELETE FROM users WHERE id = :owner",
            ):
                await session.execute(text(stmt), {"agent": _AGENT, "owner": _OWNER})
            await session.commit()

    await _cleanup()
    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 'streamrv-owner@test.local', 'Stream', 'Owner')"
            ),
            {"id": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, 'streamrv-bot', :owner, 'system:test', 'active', 'system:test')"
            ),
            {"id": _AGENT, "owner": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by) "
                "VALUES (:id, :agent, :hash, 'system:test')"
            ),
            {
                "id": f"agc_{_AGENT}",
                "agent": _AGENT,
                "hash": hashlib.sha256(plaintext.encode()).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO actor_permission_grants (id, actor_id, actor_type, permission, "
                "created_by) VALUES (:id, :agent, 'agent', 'events:read', 'system:test')"
            ),
            {"id": f"apg_{_AGENT}", "agent": _AGENT},
        )
        await session.commit()
    yield plaintext
    await _cleanup()


@asynccontextmanager
async def _client(ctx: Context) -> AsyncIterator[AsyncClient]:
    app: FastAPI = _build_app(ctx, _FULL)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _stream_until_closed(
    ctx: Context,
    headers: dict[str, str],
    probe: StreamProbe,
    change: Callable[[], Awaitable[None]],
) -> Response:
    async with _client(ctx) as client:
        # The ASGI transport returns once the body ends, so the request only
        # completes if the server closes the stream.
        request = asyncio.create_task(client.get("/events/stream", headers=headers))
        # Two polls apart by more than the recheck interval means at least one
        # re-verification has passed on the open stream.
        await probe.wait_for_items(2)
        assert not request.done(), "the stream stays open while the agent may read events"
        await change()
        return await asyncio.wait_for(request, timeout=10)


async def _sql(ctx: Context, stmt: str) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(text(stmt), {"agent": _AGENT})
        await session.commit()


def _closed_reason(resp: Response) -> str:
    assert resp.status_code == 200, resp.text
    assert "event: heartbeat" in resp.text
    tail = resp.text.rstrip("\n").rsplit("\n\n", 1)[-1]
    assert tail.startswith("event: stream_closed\n"), resp.text
    return tail.split('"reason":"', 1)[1].split('"', 1)[0]


async def test_revoking_events_read_closes_the_open_stream(
    integration_context: Context, agent_key: str, fast_stream: StreamProbe
) -> None:
    ctx = integration_context

    async def _revoke() -> None:
        await _sql(ctx, "DELETE FROM actor_permission_grants WHERE actor_id = :agent")

    resp = await _stream_until_closed(ctx, {"x-jentic-api-key": agent_key}, fast_stream, _revoke)
    assert _closed_reason(resp) == "forbidden"


async def test_disabling_the_agent_closes_the_open_stream(
    integration_context: Context, agent_key: str, fast_stream: StreamProbe
) -> None:
    ctx = integration_context

    async def _disable() -> None:
        await _sql(ctx, "UPDATE agents SET status = 'disabled' WHERE id = :agent")

    resp = await _stream_until_closed(ctx, {"x-jentic-api-key": agent_key}, fast_stream, _disable)
    assert _closed_reason(resp) == "refused"


async def test_revoking_the_access_token_closes_the_open_stream(
    integration_context: Context, agent_key: str, fast_stream: StreamProbe
) -> None:
    ctx = integration_context
    access, _refresh = await TokenService(ctx).issue_pair(_AGENT, ActorType.AGENT, [])

    async def _revoke() -> None:
        await _sql(
            ctx,
            "UPDATE access_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE actor_id = :agent",
        )

    headers = {"Authorization": f"Bearer {access}"}
    resp = await _stream_until_closed(ctx, headers, fast_stream, _revoke)
    assert _closed_reason(resp) == "refused"


async def test_stream_closes_after_oauth_sweep(
    integration_context: Context, agent_key: str, fast_stream: StreamProbe
) -> None:
    """An SSE stream opened with a user ``at_`` closes within one recheck
    after ``change_own_password``."""
    ctx = integration_context
    old_password = "streamrv-password-initial"  # pragma: allowlist secret
    new_password = "streamrv-password-rotated"  # pragma: allowlist secret

    async with ctx.admin_db.transaction() as session:
        await UserRepository.update(session, _OWNER, active=True, must_change_password=False)
        await UserSecretRepository.set_password_hash(
            session,
            _OWNER,
            password_hash=hash_password(old_password),
            created_by="system:test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session,
            _OWNER,
            permissions={"events:read"},
            granted_by=None,
            created_by="system:test",
        )

    try:
        access, _refresh = await TokenService(ctx).issue_pair(
            _OWNER, ActorType.USER, ["events:read"]
        )
        owner_identity = Identity(
            sub=_OWNER,
            email="streamrv-owner@test.local",
            actor_type=ActorType.USER,
            permissions=["events:read"],
        )

        async def _change_password() -> None:
            await AuthService(ctx).change_own_password(
                ChangePasswordPayload(
                    current_password=old_password,
                    new_password=new_password,
                ),
                identity=owner_identity,
            )

        headers = {"Authorization": f"Bearer {access}"}
        resp = await _stream_until_closed(ctx, headers, fast_stream, _change_password)
        assert _closed_reason(resp) == "refused"
    finally:
        async with ctx.admin_db.transaction() as session:
            for stmt in (
                "DELETE FROM user_permission_grants WHERE user_id = :owner",
                "DELETE FROM user_secrets WHERE user_id = :owner",
                "DELETE FROM access_tokens WHERE actor_id = :owner",
                "DELETE FROM refresh_tokens WHERE actor_id = :owner",
            ):
                await session.execute(text(stmt), {"owner": _OWNER})
