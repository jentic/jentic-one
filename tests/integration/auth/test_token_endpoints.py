"""Integration tests for OAuth token endpoints against real DB."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    AccessTokenRepository,
    ActorPermissionGrantRepository,
    AgentRepository,
    UserRepository,
)
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import TokenService, _hash_token
from jentic_one.auth.web.routers.oauth import token_endpoint
from jentic_one.auth.web.schemas.oauth import TokenRequest
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.models import ActorStatus, ActorType

pytestmark = pytest.mark.integration

# Marker for actor rows seeded by this file, so cleanup never touches
# bootstrap rows or other files' fixtures.
_SEED_MARKER = "usr_tok_seed"


@pytest.fixture()
async def clean_tokens(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AccessToken))
            await session.execute(delete(RefreshToken))
            await session.execute(delete(ActorPermissionGrant))
            await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
            await session.execute(delete(User).where(User.created_by == _SEED_MARKER))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_user(ctx: Context, user_id: str, *, active: bool = True) -> str:
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            id=user_id,
            email=f"{user_id}@tokens.test",
            first_name="Token",
            last_name="Test",
            active=active,
            created_by=_SEED_MARKER,
        )
        await session.commit()
        return user.id


async def _seed_agent(
    ctx: Context, *, owner_id: str, status: ActorStatus = ActorStatus.ACTIVE
) -> str:
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="token-test-agent",
            owner_id=owner_id,
            registered_by=owner_id,
            created_by=_SEED_MARKER,
            status=status,
        )
        await session.commit()
        return agent.id


@pytest.fixture()
def token_service(integration_context: Context) -> TokenService:
    return TokenService(integration_context)


async def test_issue_pair_lifecycle(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Full lifecycle: issue -> introspect (active) -> revoke -> introspect (inactive)."""
    user_id = await _seed_user(integration_context, "usr_test1")
    access, refresh = await token_service.issue_pair(user_id, ActorType.USER, ["read", "write"])

    assert access.startswith("at_")
    assert refresh.startswith("rt_")

    result = await token_service.introspect(access)
    assert result["active"] is True
    assert result["sub"] == user_id
    assert result["scope"] == "read write"

    await token_service.revoke(access, identity=Identity(sub=user_id, email="test@local"))

    result = await token_service.introspect(access)
    assert result["active"] is False


async def test_refresh_rotation(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Issue -> refresh -> verify new pair works, old is consumed."""
    user_id = await _seed_user(integration_context, "usr_test2")
    access1, refresh1 = await token_service.issue_pair(user_id, ActorType.USER, ["read"])

    access2, refresh2, scopes2 = await token_service.refresh(refresh1)

    assert access2.startswith("at_")
    assert refresh2.startswith("rt_")
    assert scopes2 == ["read"]
    assert access2 != access1
    assert refresh2 != refresh1

    result = await token_service.introspect(access2)
    assert result["active"] is True

    old_rt_result = await token_service.introspect(refresh1)
    assert old_rt_result["active"] is False


async def test_reuse_detection(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Issue -> refresh -> replay old refresh -> entire family revoked."""
    user_id = await _seed_user(integration_context, "usr_test3")
    _access1, refresh1 = await token_service.issue_pair(user_id, ActorType.USER, ["read"])

    access2, _refresh2, _scopes2 = await token_service.refresh(refresh1)

    with pytest.raises(InvalidGrantError, match="reuse detected"):
        await token_service.refresh(refresh1)

    result = await token_service.introspect(access2)
    assert result["active"] is False


async def test_introspect_expired_returns_inactive(
    integration_context: Context, clean_tokens: None
) -> None:
    """Expired token introspection returns inactive."""
    # Seed the actor active so this pins *expiry*, not the missing-actor
    # fail-closed path (#1136).
    user_id = await _seed_user(integration_context, "usr_expired")
    async with integration_context.admin_db.transaction() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=_hash_token("at_expired_test"),
            actor_id=user_id,
            actor_type="user",
            scopes=["read"],
            token_family_id="tfam_expired",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            created_by="usr_test",
            is_ephemeral=False,
        )

    svc = TokenService(integration_context)
    result = await svc.introspect("at_expired_test")
    assert result["active"] is False


async def test_token_endpoint_unsupported_grant_type(
    token_service: TokenService, clean_tokens: None
) -> None:
    """Invalid grant_type raises InvalidGrantError."""
    request = MagicMock()
    request.headers = {}
    response = MagicMock()
    response.headers = {}

    with pytest.raises(InvalidGrantError, match="unsupported grant_type"):
        body = TokenRequest(grant_type="invalid_grant", refresh_token=None)
        await token_endpoint(request=request, response=response, body=body, token_svc=token_service)


async def test_token_endpoint_client_credentials_grant_is_unsupported(
    token_service: TokenService, clean_tokens: None
) -> None:
    """Theme-8 Phase 2 (D3): the service-account client-credentials arm is gone,
    so ``grant_type=client_credentials`` answers ``unsupported_grant_type``."""
    request = MagicMock()
    request.headers = {}
    response = MagicMock()
    response.headers = {}

    body = TokenRequest(
        grant_type="client_credentials", client_id="sva_whatever", client_secret="jcs_x"
    )
    with pytest.raises(InvalidGrantError, match="unsupported grant_type") as exc_info:
        await token_endpoint(request=request, response=response, body=body, token_svc=token_service)
    assert exc_info.value.oauth_error_code == "unsupported_grant_type"


async def test_invalid_refresh_token(token_service: TokenService, clean_tokens: None) -> None:
    """Attempting to refresh with an invalid token raises InvalidGrantError."""
    with pytest.raises(InvalidGrantError, match="not found"):
        await token_service.refresh("rt_invalid_doesnotexist")


async def test_resolve_access_token(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """resolve_access_token returns an Identity whose scopes reflect *live* grants.

    Long-lived agent tokens (an access+refresh pair) resolve scopes from the
    actor's current ``ActorPermissionGrant`` rows, so a scope change is reflected
    immediately. Seed the grant that a real ``issue_pair`` would have derived
    from.
    """
    owner_id = await _seed_user(integration_context, "usr_resolve_owner")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    async with integration_context.admin_db.session() as session:
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent_id,
            actor_type=ActorType.AGENT,
            permission="execute",
            granted_by="usr_test",
            created_by="usr_test",
        )
        await session.commit()

    access, _refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, ["execute"])

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.active is True
    assert resolved.sub == agent_id
    assert resolved.actor_type == "agent"
    assert resolved.parent_actor_id == owner_id
    assert resolved.permissions == ["execute"]


async def test_resolve_reflects_scope_grant_without_remint(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Regression for #531: adding a scope grant takes effect on the *existing*
    token pair — no re-mint required."""
    owner_id = await _seed_user(integration_context, "usr_scope_owner")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    async with integration_context.admin_db.session() as session:
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent_id,
            actor_type=ActorType.AGENT,
            permission="apis:read",
            granted_by="usr_owner",
            created_by="usr_owner",
        )
        await session.commit()

    access, _refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, ["apis:read"])
    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.permissions == ["apis:read"]

    # Owner grants apis:write after the token was minted.
    async with integration_context.admin_db.session() as session:
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent_id,
            actor_type=ActorType.AGENT,
            permission="apis:write",
            granted_by="usr_owner",
            created_by="usr_owner",
        )
        await session.commit()

    # The same (unchanged) access token now resolves the new scope.
    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.permissions == ["apis:read", "apis:write"]


async def test_resolve_reflects_scope_revocation_without_remint(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Revoking a scope also takes effect on the existing token pair immediately."""
    owner_id = await _seed_user(integration_context, "usr_revoke_owner")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    async with integration_context.admin_db.session() as session:
        for scope in ("apis:read", "apis:write"):
            await ActorPermissionGrantRepository.grant(
                session,
                actor_id=agent_id,
                actor_type=ActorType.AGENT,
                permission=scope,
                granted_by="usr_owner",
                created_by="usr_owner",
            )
        await session.commit()

    access, _refresh = await token_service.issue_pair(
        agent_id, ActorType.AGENT, ["apis:read", "apis:write"]
    )

    async with integration_context.admin_db.session() as session:
        await ActorPermissionGrantRepository.revoke(
            session, actor_id=agent_id, permission="apis:write"
        )
        await session.commit()

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.permissions == ["apis:read"]


async def test_issue_pair_transient_lock_exhausted_raises_db_unavailable(
    token_service: TokenService, clean_tokens: None
) -> None:
    """A transient OperationalError that outlasts the retry budget surfaces as 503-mapped error.

    Drives the real ``issue_pair`` write through ``run_in_transaction`` against the
    real admin DB; ``AccessTokenRepository.create`` is forced to raise a transient
    ``OperationalError`` (the same class SQLite raises on ``database is locked``).
    After retries are exhausted it must raise ``DatabaseUnavailableError`` — the
    web layer maps that to HTTP 503 ``database_unavailable`` (see
    tests/unit/auth/web/test_oauth_db_unavailable.py), not a bare 500.
    """

    def _raise_locked(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("INSERT INTO access_tokens", {}, Exception("database is locked"))

    with (
        patch.object(AccessTokenRepository, "create", side_effect=_raise_locked),
        pytest.raises(DatabaseUnavailableError),
    ):
        await token_service.issue_pair("usr_locked", ActorType.USER, ["read"])


async def test_issue_pair_transient_lock_then_success(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """A transient lock on the first attempt is retried and the mint then succeeds."""
    user_id = await _seed_user(integration_context, "usr_retry")
    real_create = AccessTokenRepository.create
    calls = {"n": 0}

    async def _flaky_create(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("INSERT INTO access_tokens", {}, Exception("database is locked"))
        return await real_create(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(AccessTokenRepository, "create", side_effect=_flaky_create):
        access, refresh = await token_service.issue_pair(user_id, ActorType.USER, ["read"])

    assert access.startswith("at_")
    assert refresh.startswith("rt_")
    assert calls["n"] >= 2
    result = await token_service.introspect(access)
    assert result["active"] is True


async def test_revoke_refresh_token_revokes_family(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Revoking a refresh token revokes the entire token family."""
    # Seed the actor active so the inactive verdicts below can only come from
    # the revocation itself, not the missing-actor fail-closed path (#1136).
    user_id = await _seed_user(integration_context, "usr_fam1")
    access, refresh = await token_service.issue_pair(user_id, ActorType.USER, ["read"])
    assert (await token_service.introspect(access))["active"] is True

    await token_service.revoke(refresh, identity=Identity(sub=user_id, email="test@local"))

    at_result = await token_service.introspect(access)
    assert at_result["active"] is False

    rt_result = await token_service.introspect(refresh)
    assert rt_result["active"] is False


# --- actor-status kill switch (#1136) -------------------------------------
# Disabling an actor must invalidate its *outstanding* tokens immediately —
# not just block new mints. Two steps (disable + revoke family) must no
# longer be required.


async def _set_agent_status(ctx: Context, agent_id: str, status: ActorStatus) -> None:
    async with ctx.admin_db.session() as session:
        await AgentRepository.update_status(session, agent_id, status)
        await session.commit()


async def test_disable_agent_kills_outstanding_access_token(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """The #1136 regression: an unexpired, unrevoked token resolves as inactive
    the moment its agent is disabled — and works again after re-enable."""
    owner_id = await _seed_user(integration_context, "usr_kill_owner")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    access, _refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is True

    await _set_agent_status(integration_context, agent_id, ActorStatus.DISABLED)

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.active is False, "disable alone must kill the outstanding token"
    assert (await token_service.introspect(access))["active"] is False

    # The kill switch is reversible: enable restores the same token.
    await _set_agent_status(integration_context, agent_id, ActorStatus.ACTIVE)
    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is True


async def test_archived_agent_token_is_inactive(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    owner_id = await _seed_user(integration_context, "usr_arch_owner")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    access, _refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    await _set_agent_status(integration_context, agent_id, ActorStatus.ARCHIVED)

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.active is False


async def test_disabled_agent_cannot_refresh_to_fresh_tokens(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """A disabled agent holding a refresh token must not rotate its way to a
    fresh access token (otherwise the exposure window is the refresh TTL, not
    the access TTL). Re-enabling makes the same refresh token work again."""
    owner_id = await _seed_user(integration_context, "usr_rot_owner")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    _access, refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    await _set_agent_status(integration_context, agent_id, ActorStatus.DISABLED)

    with pytest.raises(InvalidGrantError, match="not active"):
        await token_service.refresh(refresh)

    # The rejected attempt must not have consumed the refresh token: after
    # re-enable, the same token still rotates normally (no reuse trip).
    await _set_agent_status(integration_context, agent_id, ActorStatus.ACTIVE)
    access2, refresh2, _scopes2 = await token_service.refresh(refresh)
    assert access2.startswith("at_")
    assert refresh2.startswith("rt_")


async def test_residual_service_account_token_is_refused(
    token_service: TokenService, clean_tokens: None
) -> None:
    """Theme-8 Phase 4: ``service_account`` is no longer an actor type, and the
    drop migration leaves residual SA token rows in place. Such a row fails
    CLOSED on every path (resolve, introspect, refresh) and never raises."""
    # A raw string, not an ActorType member: the enum value was deleted.
    retired = cast(ActorType, "service_account")
    access, refresh = await token_service.issue_pair("sva_residual", retired, [])

    assert await token_service.resolve_access_token(access) is None
    assert (await token_service.introspect(access))["active"] is False
    assert (await token_service.introspect(refresh))["active"] is False
    with pytest.raises(InvalidGrantError, match="not active"):
        await token_service.refresh(refresh)


async def test_deactivated_user_token_is_inactive(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    user_id = await _seed_user(integration_context, "usr_deact")
    access, refresh = await token_service.issue_pair(user_id, ActorType.USER, ["openid"])

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is True

    async with integration_context.admin_db.session() as session:
        await UserRepository.update(session, user_id, active=False)
        await session.commit()

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.active is False
    with pytest.raises(InvalidGrantError, match="not active"):
        await token_service.refresh(refresh)


async def test_token_for_missing_actor_row_fails_closed(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """A token whose actor row was hard-deleted must not remain usable."""
    async with integration_context.admin_db.transaction() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=_hash_token("at_orphaned_actor"),
            actor_id="agnt_never_existed",
            actor_type="agent",
            scopes=[],
            token_family_id="tfam_orphan",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by="usr_test",
            is_ephemeral=False,
        )

    resolved = await token_service.resolve_access_token("at_orphaned_actor")
    assert resolved is not None
    assert resolved.active is False


# --- owned agents follow their owner's account status ---------------------
# An owned agent's tokens stop resolving while its owner is disabled or
# deleted, and work again once the owner is re-enabled. The agent's own
# status is never changed; an ownerless agent depends on its own status only.


async def _disable_owner(ctx: Context, user_id: str) -> None:
    async with ctx.admin_db.session() as session:
        await UserRepository.disable(session, user_id)
        await session.commit()


async def _enable_owner(ctx: Context, user_id: str) -> None:
    async with ctx.admin_db.session() as session:
        await UserRepository.enable(session, user_id)
        await session.commit()


async def _delete_owner(ctx: Context, user_id: str) -> None:
    """The row-level effect of ``UserService.delete`` (a soft delete)."""
    async with ctx.admin_db.session() as session:
        await UserRepository.update(
            session, user_id, email=f"deleted-{user_id}@local", active=False
        )
        await session.commit()


async def _agent_status(ctx: Context, agent_id: str) -> str:
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
        assert agent is not None
        return agent.status


async def test_owner_disable_kills_agent_access_token_until_re_enabled(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """Disabling the owner makes the agent's outstanding token inactive on
    resolve and introspection; re-enabling the owner restores the same token."""
    owner_id = await _seed_user(integration_context, "usr_own_dis")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    access, _refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    await _disable_owner(integration_context, owner_id)
    with structlog.testing.capture_logs() as logs:
        resolved = await token_service.resolve_access_token(access)
    assert resolved is not None
    assert resolved.active is False
    assert (await token_service.introspect(access))["active"] is False
    owner_inactive = [log for log in logs if log["event"] == "agent_owner_inactive"]
    assert len(owner_inactive) == 1
    assert owner_inactive[0]["log_level"] == "warning"
    assert owner_inactive[0]["agent_id"] == agent_id
    assert owner_inactive[0]["owner_id"] == owner_id
    assert await _agent_status(integration_context, agent_id) == ActorStatus.ACTIVE

    await _enable_owner(integration_context, owner_id)
    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is True
    assert resolved.parent_actor_id == owner_id
    assert (await token_service.introspect(access))["active"] is True


async def test_owner_delete_kills_agent_access_token(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    owner_id = await _seed_user(integration_context, "usr_own_del")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    access, refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    await _delete_owner(integration_context, owner_id)

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is False
    assert (await token_service.introspect(refresh))["active"] is False
    with pytest.raises(InvalidGrantError, match="not active"):
        await token_service.refresh(refresh)


async def test_inactive_owner_agent_cannot_refresh_until_re_enabled(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """While the owner is disabled the agent can't rotate its refresh token;
    the refused attempt doesn't consume it, so it rotates after re-enable."""
    owner_id = await _seed_user(integration_context, "usr_own_rot")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id)
    _access, refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    await _disable_owner(integration_context, owner_id)
    with pytest.raises(InvalidGrantError, match="not active"):
        await token_service.refresh(refresh)

    await _enable_owner(integration_context, owner_id)
    access2, _refresh2, _scopes2 = await token_service.refresh(refresh)
    resolved = await token_service.resolve_access_token(access2)
    assert resolved is not None and resolved.active is True


async def test_ownerless_agent_access_token_is_unaffected(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """An agent with no owner resolves on its own status alone."""
    async with integration_context.admin_db.session() as session:
        agent = Agent(
            name="ownerless-agent",
            owner_id=None,
            registered_by=_SEED_MARKER,
            created_by=_SEED_MARKER,
            status=ActorStatus.ACTIVE,
        )
        session.add(agent)
        await session.commit()
        agent_id = agent.id
    access, refresh = await token_service.issue_pair(agent_id, ActorType.AGENT, [])

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is True
    assert resolved.parent_actor_id is None
    assert (await token_service.introspect(access))["active"] is True
    access2, _refresh2, _scopes2 = await token_service.refresh(refresh)
    assert access2.startswith("at_")


async def test_user_token_is_unaffected_by_agent_owner_check(
    token_service: TokenService, integration_context: Context, clean_tokens: None
) -> None:
    """A user's own token keeps resolving on the user's own status: owning an
    agent, or another owner being disabled, changes nothing for it."""
    user_id = await _seed_user(integration_context, "usr_own_self")
    await _seed_agent(integration_context, owner_id=user_id)
    other_owner = await _seed_user(integration_context, "usr_own_other")
    await _seed_agent(integration_context, owner_id=other_owner)
    access, refresh = await token_service.issue_pair(user_id, ActorType.USER, ["openid"])

    await _disable_owner(integration_context, other_owner)

    resolved = await token_service.resolve_access_token(access)
    assert resolved is not None and resolved.active is True
    assert resolved.parent_actor_id is None
    assert (await token_service.introspect(access))["active"] is True
    access2, _refresh2, _scopes2 = await token_service.refresh(refresh)
    assert access2.startswith("at_")
