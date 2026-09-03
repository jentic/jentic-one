"""SQLite integration matrix for RFC 7009 token revocation (G11, phase 3).

End-to-end over the real web route (``POST /oauth/revoke``, form-encoded arm)
against real grant-channel tokens minted through the consent→code→exchange
path (:mod:`tests.integration.auth.seeds`). Pins the decided semantics
(Manuel, 2026-09-03):

- access-token revoke → that token dies, the grant survives;
- refresh-token revoke → FULL disconnect: grant row revoked + every token of
  the grant dead on the live resolver + the shared ``oauth_grant.revoked``
  event with ``data.reason=rfc7009_client_revocation`` (same sweep as the UI
  ``:revoke`` and the G10 transfer — one revocation semantics everywhere);
- no-oracle posture: unknown/foreign/replayed tokens answer 200 and revoke
  nothing; only a missing ``token`` is a 400;
- the ``server.mcp.oauth.enabled`` gate (plain 404) and the legacy
  JSON+bearer arm (the CLI-logout contract) staying intact.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from sqlalchemy import select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.services.oauth_revocation_service import (
    RFC7009_CLIENT_REVOCATION_REASON,
)
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_CLIENT_ID = seeds.CLIENT_ID
_FOREIGN_CLIENT_ID = "oc_rfc7009_foreign"
_REVOKE_PATH = "/oauth/revoke"


@pytest.fixture()
async def mcp_oauth_enabled(integration_context: Context) -> AsyncGenerator[None, None]:
    """Flip the instance gate on for the test, restoring the configured value."""
    gate = integration_context.config.server.mcp.oauth
    before = gate.enabled
    gate.enabled = True
    yield
    gate.enabled = before


def _make_app(ctx: Context) -> FastAPI:
    """The oauth router with the real auth-surface error handlers + verifier."""
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()

    async def _verify(token: str, request: Request) -> Identity:
        identity = await TokenService(ctx).resolve_access_token(token)
        if identity is None or not identity.active:
            raise ValueError("invalid token")  # resolve_identity maps to 401
        return identity

    app.state.verify_token = _verify
    return app


def _web_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _mint(
    ctx: Context, *, user_suffix: str, client_id: str = _CLIENT_ID
) -> tuple[str, str, str]:
    """Seed user+agent(+client) and mint grant-channel tokens. -> (grant, at, rt)."""
    user_id = await seeds.seed_user(ctx, f"usr_r7009_{user_suffix}")
    agent_id = await seeds.seed_agent(ctx, owner_id=user_id, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"], client_id=client_id)
    grant_id, access, refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=user_id, agent_id=agent_id, grant_scopes=["apis:read"], client_id=client_id
    )
    return grant_id, access, refresh


async def _grant_status(ctx: Context, grant_id: str) -> str:
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None
    return str(grant.status)


async def _rfc7009_events(ctx: Context, grant_id: str) -> list[Event]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == EventType.OAUTH_GRANT_REVOKED)
        )
        return [e for e in result.scalars().all() if (e.data or {}).get("grant_id") == grant_id]


async def _grant_revoke_audits(ctx: Context, grant_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_type == "oauth_grant",
                AuditEntry.target_id == grant_id,
                AuditEntry.action == "revoke",
            )
        )
        return list(result.scalars().all())


# --- refresh-token revoke: the full disconnect --------------------------------


@pytest.mark.parametrize("hint", ["refresh_token", "access_token", None, "bogus_hint"])
async def test_refresh_revoke_full_disconnect_regardless_of_hint(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None, hint: str | None
) -> None:
    """Any hint arm (right, wrong, none, unknown) lands the same full
    disconnect: grant row revoked, BOTH token families dead on the live
    resolver, refresh fails closed, and the shared event carries the
    rfc7009 cause-in-data reason."""
    ctx = integration_context
    grant_id, access, refresh = await _mint(ctx, user_suffix=f"fd_{hint}")

    form = {"token": refresh, "client_id": _CLIENT_ID}
    if hint is not None:
        form["token_type_hint"] = hint
    async with _web_client(_make_app(ctx)) as client:
        resp = await client.post(_REVOKE_PATH, data=form)
    assert resp.status_code == 200
    assert resp.content == b""

    # The grant row itself is revoked — reconnecting needs fresh consent.
    assert await _grant_status(ctx, grant_id) == "revoked"

    # Both token families are dead: access no longer resolves, refresh fails
    # closed (grant gate + revoked family).
    token_svc = TokenService(ctx)
    assert await token_svc.resolve_access_token(access) is None
    with pytest.raises(InvalidGrantError):
        await token_svc.refresh(refresh, client_id=_CLIENT_ID)

    # One audit row + one event, cause-stamped, attributed to the token's agent.
    audits = await _grant_revoke_audits(ctx, grant_id)
    assert len(audits) == 1
    assert audits[0].reason == "oauth grant revoked: client revoked its refresh token (RFC 7009)"
    events = await _rfc7009_events(ctx, grant_id)
    assert len(events) == 1
    assert (events[0].data or {}).get("reason") == RFC7009_CLIENT_REVOCATION_REASON


async def test_refresh_revoke_is_idempotent_on_replay(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None
) -> None:
    """Replaying the same (now dead) refresh token answers 200 and writes no
    second audit/event — the no-oracle posture holds after revocation too."""
    ctx = integration_context
    grant_id, _access, refresh = await _mint(ctx, user_suffix="replay")

    async with _web_client(_make_app(ctx)) as client:
        first = await client.post(_REVOKE_PATH, data={"token": refresh, "client_id": _CLIENT_ID})
        second = await client.post(_REVOKE_PATH, data={"token": refresh, "client_id": _CLIENT_ID})
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(await _rfc7009_events(ctx, grant_id)) == 1
    assert len(await _grant_revoke_audits(ctx, grant_id)) == 1


# --- access-token revoke: single token dies, grant survives -------------------


@pytest.mark.parametrize("hint", ["access_token", "refresh_token", None])
async def test_access_revoke_kills_token_but_grant_survives(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None, hint: str | None
) -> None:
    """Access revoke (any hint arm) kills exactly that token: the grant stays
    active, the refresh token still rotates, and no grant event is written."""
    ctx = integration_context
    grant_id, access, refresh = await _mint(ctx, user_suffix=f"at_{hint}")

    form = {"token": access, "client_id": _CLIENT_ID}
    if hint is not None:
        form["token_type_hint"] = hint
    async with _web_client(_make_app(ctx)) as client:
        resp = await client.post(_REVOKE_PATH, data=form)
    assert resp.status_code == 200

    token_svc = TokenService(ctx)
    revoked = await token_svc.resolve_access_token(access)
    # Only the token row died (no grant/client gate tripped): the resolver
    # reports the row as inactive rather than unknown.
    assert revoked is not None and revoked.active is False
    assert await _grant_status(ctx, grant_id) == "active"
    # The client can still re-obtain access: the refresh channel survives.
    new_access, _new_refresh = await token_svc.refresh(refresh, client_id=_CLIENT_ID)
    resolved = await token_svc.resolve_access_token(new_access)
    assert resolved is not None and resolved.active is True
    assert await _rfc7009_events(ctx, grant_id) == []


# --- no-oracle arms ------------------------------------------------------------


async def test_foreign_client_token_is_200_noop_with_grant_intact(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None
) -> None:
    """A client presenting ANOTHER client's tokens revokes nothing — 200
    either way, grant active, tokens still live (lineage binding is the
    public-client 'authentication')."""
    ctx = integration_context
    grant_id, access, refresh = await _mint(ctx, user_suffix="foreign")
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"], client_id=_FOREIGN_CLIENT_ID)

    async with _web_client(_make_app(ctx)) as client:
        for token in (access, refresh):
            resp = await client.post(
                _REVOKE_PATH, data={"token": token, "client_id": _FOREIGN_CLIENT_ID}
            )
            assert resp.status_code == 200

    assert await _grant_status(ctx, grant_id) == "active"
    token_svc = TokenService(ctx)
    resolved = await token_svc.resolve_access_token(access)
    assert resolved is not None and resolved.active is True
    assert await _rfc7009_events(ctx, grant_id) == []


async def test_missing_client_id_is_200_noop(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None
) -> None:
    """Without client_id no lineage can match: 200, nothing revoked."""
    ctx = integration_context
    grant_id, _access, refresh = await _mint(ctx, user_suffix="nocid")

    async with _web_client(_make_app(ctx)) as client:
        resp = await client.post(_REVOKE_PATH, data={"token": refresh})
    assert resp.status_code == 200
    assert await _grant_status(ctx, grant_id) == "active"


async def test_unknown_token_is_200(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None
) -> None:
    async with _web_client(_make_app(integration_context)) as client:
        resp = await client.post(
            _REVOKE_PATH, data={"token": "rt_never_issued", "client_id": _CLIENT_ID}
        )
    assert resp.status_code == 200


async def test_missing_token_is_400_invalid_request(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None
) -> None:
    """The endpoint's only 400 — RFC 6749 §5.2 dialect per RFC 7009 §2.2.1."""
    async with _web_client(_make_app(integration_context)) as client:
        resp = await client.post(_REVOKE_PATH, data={"client_id": _CLIENT_ID})
    assert resp.status_code == 400
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "token is required",
    }


# --- the gate + rate limit ------------------------------------------------------


async def test_disabled_arm_is_plain_404_and_revokes_nothing(
    integration_context: Context, clean_grants: None
) -> None:
    """server.mcp.oauth.enabled=false (the default): the RFC 7009 arm answers
    the framework's plain 404 (DCR-door posture) and the tokens stay live."""
    ctx = integration_context
    assert ctx.config.server.mcp.oauth.enabled is False
    grant_id, access, refresh = await _mint(ctx, user_suffix="gate")

    async with _web_client(_make_app(ctx)) as client:
        resp = await client.post(_REVOKE_PATH, data={"token": refresh, "client_id": _CLIENT_ID})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert await _grant_status(ctx, grant_id) == "active"
    resolved = await TokenService(ctx).resolve_access_token(access)
    assert resolved is not None and resolved.active is True


async def test_form_arm_is_rate_limited_in_its_own_bucket(
    integration_context: Context, clean_grants: None, mcp_oauth_enabled: None
) -> None:
    """Over-quota form requests answer 429 + Retry-After (namespaced per-IP
    bucket, the 3a-2 fix-wave pattern)."""
    ctx = integration_context
    app = _make_app(ctx)
    # Pin a 1-request bucket on the app-state cache the route consults.
    app.state._revocation_limiter = RateLimiter(
        app.state.auth_state_backend, default_rpm=1, burst=1, namespace="oauth-revocation"
    )
    async with _web_client(app) as client:
        first = await client.post(
            _REVOKE_PATH, data={"token": "rt_whatever", "client_id": _CLIENT_ID}
        )
        second = await client.post(
            _REVOKE_PATH, data={"token": "rt_whatever", "client_id": _CLIENT_ID}
        )
    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


# --- the legacy JSON+bearer arm (CLI-logout regression) --------------------------


async def test_json_arm_bearer_self_revoke_contract_intact(
    integration_context: Context, clean_grants: None
) -> None:
    """The pre-G11 contract (the `jentic logout` shape): JSON body + bearer,
    revoking the caller's own access token — 200, token dead. Works with the
    MCP OAuth gate OFF (it is not part of that surface)."""
    ctx = integration_context
    assert ctx.config.server.mcp.oauth.enabled is False
    _grant_id, access, _refresh = await _mint(ctx, user_suffix="legacy")

    async with _web_client(_make_app(ctx)) as client:
        resp = await client.post(
            _REVOKE_PATH,
            json={"token": access},
            headers={"Authorization": f"Bearer {access}"},
        )
    assert resp.status_code == 200
    revoked = await TokenService(ctx).resolve_access_token(access)
    assert revoked is not None and revoked.active is False


async def test_json_arm_still_requires_bearer(
    integration_context: Context, clean_grants: None
) -> None:
    async with _web_client(_make_app(integration_context)) as client:
        resp = await client.post(_REVOKE_PATH, json={"token": "at_x"})
    assert resp.status_code == 401
