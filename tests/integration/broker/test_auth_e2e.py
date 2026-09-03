"""Integration tests for the broker's dual-token validation against a live admin DB.

Seeds an opaque ``access_tokens`` row and asserts the ``DualTokenValidator``:
  - resolves the opaque token via the in-process admin-DB resolver, and
  - validates a short-TTL self-contained signed JWT **without** any DB lookup.

This exercises the §03 wiring (``install_broker_auth`` builds the same dispatcher)
end-to-end through the real ``InProcessTokenResolver``.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.admin.repos.actor_scope_grant_repo import ActorScopeGrantRepository
from jentic_one.admin.repos.refresh_token_repo import RefreshTokenRepository
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.broker.services.auth import DualTokenValidator, JwtTokenValidator, JwtVerifier
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

pytestmark = pytest.mark.integration

_JWT_SECRET = "integration-test-secret-key-32-bytes!!"  # pragma: allowlist secret
_SEED_MARKER = "usr_broker_e2e_seed"


@pytest.fixture()
async def clean_access_tokens(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(AccessToken))
            await session.execute(delete(RefreshToken))
            await session.execute(delete(ActorScopeGrant))
            await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
            await session.execute(
                delete(ServiceAccount).where(ServiceAccount.created_by == _SEED_MARKER)
            )
            await session.execute(delete(User).where(User.created_by == _SEED_MARKER))
            await session.execute(delete(OAuthClient).where(OAuthClient.created_by == _SEED_MARKER))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


def _dual(admin_db: DatabaseSession, *, with_jwt: bool = True) -> DualTokenValidator:
    opaque = CachedTokenValidator(resolver=InProcessTokenResolver(admin_db))
    jwt_validator = (
        JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET)) if with_jwt else None
    )
    return DualTokenValidator(opaque=opaque, jwt=jwt_validator)


async def _seed_agent(admin_db: DatabaseSession, agent_id: str, *, status: str = "active") -> None:
    """Seed the actor row that token resolution re-checks on every resolve (#1136)."""
    async with admin_db.session() as session:
        session.add(
            Agent(
                id=agent_id,
                name=f"e2e-{agent_id}",
                registered_by=_SEED_MARKER,
                created_by=_SEED_MARKER,
                status=status,
            )
        )
        await session.commit()


async def _set_agent_status(admin_db: DatabaseSession, agent_id: str, status: str) -> None:
    async with admin_db.session() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(status=status))
        await session.commit()


async def _seed_user_row(admin_db: DatabaseSession, user_id: str, *, active: bool = True) -> None:
    async with admin_db.session() as session:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@e2e.test",
                first_name="Broker",
                last_name="E2E",
                active=active,
                created_by=_SEED_MARKER,
            )
        )
        await session.commit()


async def _seed_service_account_row(
    admin_db: DatabaseSession, sa_id: str, *, owner_id: str, status: str = "active"
) -> None:
    async with admin_db.session() as session:
        session.add(
            ServiceAccount(
                id=sa_id,
                name=f"e2e-{sa_id}",
                owner_id=owner_id,
                registered_by=owner_id,
                created_by=_SEED_MARKER,
                status=status,
            )
        )
        await session.commit()


async def _seed_opaque_token(
    admin_db: DatabaseSession,
    *,
    plaintext: str,
    actor_id: str = "agnt_opaque",
    actor_type: str = "agent",
    oauth_client_id: str | None = None,
) -> None:
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    async with admin_db.session() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=token_hash,
            actor_id=actor_id,
            actor_type=actor_type,
            scopes=[BROKER_EXECUTE_SCOPE],
            token_family_id="fam_test",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by=actor_id,
            is_ephemeral=True,
            oauth_client_id=oauth_client_id,
        )
        await session.commit()


async def test_opaque_token_resolves_via_db(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    await _seed_agent(admin_db, "agnt_opaque")
    await _seed_opaque_token(admin_db, plaintext="at_live_opaque")

    resolved = await _dual(admin_db).validate("at_live_opaque")

    assert resolved.sub == "agnt_opaque"
    assert BROKER_EXECUTE_SCOPE in resolved.permissions


async def test_signed_jwt_validates_without_db_lookup(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    # No row seeded — a JWT must validate purely by signature.
    exp = int((datetime.now(UTC) + timedelta(minutes=2)).timestamp())
    token = jwt.encode(
        {
            "sub": "agnt_jwt",
            "exp": exp,
            "actor_type": "agent",
            "scopes": [BROKER_EXECUTE_SCOPE],
        },
        _JWT_SECRET,
        algorithm="HS256",
    )

    resolved = await _dual(admin_db).validate(token)

    assert resolved.sub == "agnt_jwt"
    assert resolved.permissions == [BROKER_EXECUTE_SCOPE]


async def test_unknown_opaque_token_is_rejected(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    with pytest.raises(TokenValidationError):
        await _dual(admin_db).validate("at_does_not_exist")


async def test_jwt_without_actor_type_fails_closed(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """A validly-signed JWT missing ``actor_type`` is refused, not assumed AGENT (#864)."""
    exp = int((datetime.now(UTC) + timedelta(minutes=2)).timestamp())
    token = jwt.encode({"sub": "agnt_jwt", "exp": exp}, _JWT_SECRET, algorithm="HS256")

    with pytest.raises(TokenValidationError, match="jwt_actor_type_missing"):
        await _dual(admin_db).validate(token)


async def test_jwt_claiming_toolkit_actor_type_is_rejected(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """A signed JWT can't mint a toolkit identity with zero DB backing (#868)."""
    exp = int((datetime.now(UTC) + timedelta(minutes=2)).timestamp())
    token = jwt.encode(
        {"sub": "tk_x", "exp": exp, "actor_type": "toolkit"},
        _JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(TokenValidationError, match="jwt_actor_type_not_allowed"):
        await _dual(admin_db).validate(token)


async def _seed_pair_and_grants(
    admin_db: DatabaseSession,
    *,
    plaintext: str,
    actor_id: str,
    family_id: str,
    snapshot: list[str],
    grants: list[str],
) -> None:
    """Seed a long-lived access+refresh pair (is_ephemeral=False) plus live grants."""
    now = datetime.now(UTC)
    async with admin_db.session() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            actor_id=actor_id,
            actor_type="agent",
            scopes=snapshot,
            token_family_id=family_id,
            expires_at=now + timedelta(hours=1),
            created_by=actor_id,
            is_ephemeral=False,
        )
        await RefreshTokenRepository.create(
            session,
            token_hash=hashlib.sha256((plaintext + "_rt").encode()).hexdigest(),
            actor_id=actor_id,
            actor_type="agent",
            scopes=snapshot,
            token_family_id=family_id,
            expires_at=now + timedelta(days=7),
            created_by=actor_id,
        )
        for scope in grants:
            await ActorScopeGrantRepository.grant(
                session,
                actor_id=actor_id,
                actor_type="agent",
                scope=scope,
                granted_by="usr_owner",
                created_by="usr_owner",
            )
        await session.commit()


async def test_long_lived_token_resolves_live_grants(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """A long-lived agent token (is_ephemeral=False) resolves the actor's
    current grants, not the frozen snapshot — the broker sees scope edits."""
    await _seed_agent(admin_db, "agnt_ll")
    await _seed_pair_and_grants(
        admin_db,
        plaintext="at_longlived",
        actor_id="agnt_ll",
        family_id="fam_ll",
        snapshot=["apis:read"],
        grants=["apis:read", "apis:write"],
    )

    resolved = await _dual(admin_db).validate("at_longlived")

    assert resolved.sub == "agnt_ll"
    assert sorted(resolved.permissions) == ["apis:read", "apis:write"]


async def test_ephemeral_minted_token_keeps_downscoped_snapshot(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """An ephemeral minted token (is_ephemeral=True) must NOT be re-broadened to
    the actor's full grants — its downscoped snapshot is a security guarantee."""
    await _seed_agent(admin_db, "agnt_eph")
    now = datetime.now(UTC)
    async with admin_db.session() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=hashlib.sha256(b"at_minted_ephemeral").hexdigest(),
            actor_id="agnt_eph",
            actor_type="agent",
            scopes=[BROKER_EXECUTE_SCOPE],
            token_family_id="fam_eph",
            expires_at=now + timedelta(minutes=5),
            created_by="agnt_eph",
            is_ephemeral=True,
        )
        # Broader live grants exist, but the token is flagged ephemeral.
        for scope in (BROKER_EXECUTE_SCOPE, "apis:write"):
            await ActorScopeGrantRepository.grant(
                session,
                actor_id="agnt_eph",
                actor_type="agent",
                scope=scope,
                granted_by="usr_owner",
                created_by="usr_owner",
            )
        await session.commit()

    resolved = await _dual(admin_db).validate("at_minted_ephemeral")

    assert resolved.sub == "agnt_eph"
    assert resolved.permissions == [BROKER_EXECUTE_SCOPE]


# --- actor-status kill switch on the execute path (#1136) ------------------


async def test_disabled_agent_token_rejected_by_broker(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """A disabled agent's unexpired, unrevoked token must not validate — the
    broker resolver re-checks the agent row, not just revocation + expiry."""
    await _seed_agent(admin_db, "agnt_opaque", status="disabled")
    await _seed_opaque_token(admin_db, plaintext="at_disabled_agent")

    with pytest.raises(TokenValidationError, match="token_inactive"):
        await _dual(admin_db).validate("at_disabled_agent")


@pytest.mark.parametrize("status", ["pending", "rejected", "archived"])
async def test_non_active_agent_token_rejected_by_broker(
    admin_db: DatabaseSession, clean_access_tokens: None, status: str
) -> None:
    await _seed_agent(admin_db, "agnt_opaque", status=status)
    await _seed_opaque_token(admin_db, plaintext="at_non_active_agent")

    with pytest.raises(TokenValidationError):
        await _dual(admin_db).validate("at_non_active_agent")


async def test_token_with_missing_agent_row_rejected_by_broker(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """No agent row at all fails closed (hard-deleted actor, orphaned token)."""
    await _seed_opaque_token(admin_db, plaintext="at_orphan_agent")

    with pytest.raises(TokenValidationError):
        await _dual(admin_db).validate("at_orphan_agent")


@pytest.mark.parametrize("user_active,should_pass", [(True, True), (False, False)])
async def test_user_token_follows_user_active_flag(
    admin_db: DatabaseSession, clean_access_tokens: None, user_active: bool, should_pass: bool
) -> None:
    """The SQL's `user` CASE branch: the boolean `users.active` column is
    normalised to the shared status string — dialect-sensitive, so pinned on
    both backends via the parametrized backend fixtures."""
    await _seed_user_row(admin_db, "usr_broker", active=user_active)
    await _seed_opaque_token(
        admin_db, plaintext="at_user_token", actor_id="usr_broker", actor_type="user"
    )

    if should_pass:
        resolved = await _dual(admin_db).validate("at_user_token")
        assert resolved.sub == "usr_broker"
    else:
        with pytest.raises(TokenValidationError, match="token_inactive"):
            await _dual(admin_db).validate("at_user_token")


@pytest.mark.parametrize("sa_status,should_pass", [("active", True), ("disabled", False)])
async def test_service_account_token_follows_sa_status(
    admin_db: DatabaseSession, clean_access_tokens: None, sa_status: str, should_pass: bool
) -> None:
    """The SQL's `service_account` CASE branch."""
    await _seed_user_row(admin_db, "usr_sa_owner")
    await _seed_service_account_row(
        admin_db, "sva_broker", owner_id="usr_sa_owner", status=sa_status
    )
    await _seed_opaque_token(
        admin_db, plaintext="at_sa_token", actor_id="sva_broker", actor_type="service_account"
    )

    if should_pass:
        resolved = await _dual(admin_db).validate("at_sa_token")
        assert resolved.sub == "sva_broker"
    else:
        with pytest.raises(TokenValidationError, match="token_inactive"):
            await _dual(admin_db).validate("at_sa_token")


async def test_disable_mid_life_kills_token_after_cache_ttl(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """The issue's regression pin: disable an agent while its token is in use;
    the broker keeps honouring the *cached* verdict only until the verdict-cache
    TTL lapses, then rejects — disable alone kills within ~the cache TTL."""
    await _seed_agent(admin_db, "agnt_opaque", status="active")
    await _seed_opaque_token(admin_db, plaintext="at_kill_me")

    validator = CachedTokenValidator(
        resolver=InProcessTokenResolver(admin_db), cache_ttl_seconds=0.05
    )

    resolved = await validator.validate("at_kill_me")
    assert resolved.sub == "agnt_opaque"

    await _set_agent_status(admin_db, "agnt_opaque", "disabled")

    # Within the TTL the stale cached verdict may still pass; after it lapses
    # the resolver re-checks the actor row and the token dies.
    await asyncio.sleep(0.06)
    with pytest.raises(TokenValidationError, match="token_inactive"):
        await validator.validate("at_kill_me")

    # Re-enable: the same token validates again once the negative verdict ages out.
    await _set_agent_status(admin_db, "agnt_opaque", "active")
    await asyncio.sleep(0.06)
    resolved = await validator.validate("at_kill_me")
    assert resolved.sub == "agnt_opaque"


# --- D7 approval gate at the broker resolver (PR #1218 MAJOR-1) -------------


async def _seed_oauth_client_row(
    admin_db: DatabaseSession,
    *,
    client_id: str,
    approval_status: str = "approved",
    active: bool = True,
) -> None:
    async with admin_db.session() as session:
        session.add(
            OAuthClient(
                id=f"oac_e2e_{client_id}"[:30],
                client_id=client_id,
                client_secret_hash=None,
                token_endpoint_auth_method="none",
                name=f"e2e-{client_id}",
                redirect_uris=["https://client.e2e.test/cb"],
                active=active,
                approval_status=approval_status,
                created_by=_SEED_MARKER,
            )
        )
        await session.commit()


async def _set_oauth_client_state(
    admin_db: DatabaseSession, client_id: str, *, approval_status: str, active: bool
) -> None:
    async with admin_db.session() as session:
        await session.execute(
            update(OAuthClient)
            .where(OAuthClient.client_id == client_id)
            .values(approval_status=approval_status, active=active)
        )
        await session.commit()


async def test_denied_client_token_rejected_even_if_active(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
    """A token minted while its issuing client was approved must stop resolving
    once the row is denied — even when ``active`` is somehow force-set true
    (the deny → PATCH-active pincer from the #1218 review). The broker's raw
    SQL gate checks approval_status independently of active."""
    await _seed_agent(admin_db, "agnt_opaque")
    await _seed_oauth_client_row(admin_db, client_id="oc_e2e_app")
    await _seed_opaque_token(admin_db, plaintext="at_client_channel", oauth_client_id="oc_e2e_app")

    # Sanity: resolves while the client is approved + active.
    resolved = await _dual(admin_db).validate("at_client_channel")
    assert resolved.sub == "agnt_opaque"

    # Deny, but leave the kill switch armed (the invariant-violating state).
    await _set_oauth_client_state(admin_db, "oc_e2e_app", approval_status="denied", active=True)
    with pytest.raises(TokenValidationError, match="token_inactive"):
        await _dual(admin_db).validate("at_client_channel")


@pytest.mark.parametrize(
    ("approval_status", "active"),
    [
        ("pending", True),
        ("pending", False),
        ("denied", False),
        ("approved", False),
    ],
)
async def test_client_gate_matrix_rejected_by_broker(
    admin_db: DatabaseSession,
    clean_access_tokens: None,
    approval_status: str,
    active: bool,
) -> None:
    """Every non-(approved+active) client state fails the broker gate closed."""
    await _seed_agent(admin_db, "agnt_opaque")
    await _seed_oauth_client_row(
        admin_db, client_id="oc_e2e_gated", approval_status=approval_status, active=active
    )
    await _seed_opaque_token(admin_db, plaintext="at_gated_channel", oauth_client_id="oc_e2e_gated")

    with pytest.raises(TokenValidationError, match="token_inactive"):
        await _dual(admin_db).validate("at_gated_channel")
