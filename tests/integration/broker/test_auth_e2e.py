"""Integration tests for the broker's dual-token validation against a live admin DB.

Seeds an opaque ``access_tokens`` row and asserts the ``DualTokenValidator``:
  - resolves the opaque token via the in-process admin-DB resolver, and
  - validates a short-TTL self-contained signed JWT **without** any DB lookup.

This exercises the §03 wiring (``install_broker_auth`` builds the same dispatcher)
end-to-end through the real ``InProcessTokenResolver``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
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


@pytest.fixture()
async def clean_access_tokens(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(AccessToken))
            await session.execute(delete(RefreshToken))
            await session.execute(delete(ActorScopeGrant))
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


async def _seed_opaque_token(admin_db: DatabaseSession, *, plaintext: str) -> None:
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    async with admin_db.session() as session:
        await AccessTokenRepository.create(
            session,
            token_hash=token_hash,
            actor_id="agnt_opaque",
            actor_type="agent",
            scopes=[BROKER_EXECUTE_SCOPE],
            token_family_id="fam_test",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by="agnt_opaque",
            is_ephemeral=True,
        )
        await session.commit()


async def test_opaque_token_resolves_via_db(
    admin_db: DatabaseSession, clean_access_tokens: None
) -> None:
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
