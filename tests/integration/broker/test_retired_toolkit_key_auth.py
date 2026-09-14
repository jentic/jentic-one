"""Integration tests for retired ``jntc_live_`` toolkit-key authentication.

Theme-5 Phase 4: the retirement job copies each key's SHA-256 lookup digest
into ``service_account_credentials.api_key_hash``, so the unchanged plaintext
resolves — through ``ApiKeyResolver`` — as the successor service account.
Seeds the admin DB (a user, a service account, its credential digest, and its
scope grant), then asserts the resolver maps a ``jntc_live_`` plaintext to a
service-account ``Identity`` (with a deprecation warning) and rejects
disabled accounts / unknown keys.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator

import pytest
import structlog
from sqlalchemy import text

from jentic_one.control.repos.toolkit_key_gen import generate_toolkit_key
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

pytestmark = pytest.mark.integration

_OWNER = "usr_rtka_owner"
_ACTIVE_SA = "sva_rtka_active"
_DISABLED_SA = "sva_rtka_disabled"


@pytest.fixture()
async def clean_tables(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove the admin rows this module seeds, before and after."""

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text(
                    "DELETE FROM actor_scope_grants "
                    "WHERE actor_id IN ('sva_rtka_active', 'sva_rtka_disabled')"
                )
            )
            await session.execute(
                text(
                    "DELETE FROM service_account_credentials "
                    "WHERE service_account_id IN ('sva_rtka_active', 'sva_rtka_disabled')"
                )
            )
            await session.execute(
                text(
                    "DELETE FROM service_accounts "
                    "WHERE id IN ('sva_rtka_active', 'sva_rtka_disabled')"
                )
            )
            await session.execute(text("DELETE FROM users WHERE id = :owner"), {"owner": _OWNER})
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _seed_service_account(
    admin_db: DatabaseSession,
    *,
    service_account_id: str,
    status: str = "active",
) -> str:
    """Seed a service account carrying a retired key's digest; return the plaintext.

    Mirrors what the retirement job writes: the ``jntc_live_`` plaintext's
    SHA-256 digest lands in ``service_account_credentials.api_key_hash`` and
    the account holds exactly ``capabilities:execute``.
    """
    plaintext, _hashed, _preview, _lookup = generate_toolkit_key()
    api_key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 'rtka-owner@test.local', 'Rita', 'K') ON CONFLICT DO NOTHING"
            ),
            {"id": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO service_accounts "
                "(id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, :name, :owner, 'system:test', :status, 'system:test')"
            ),
            {
                "id": service_account_id,
                "name": f"toolkit-key:{service_account_id}",
                "owner": _OWNER,
                "status": status,
            },
        )
        await session.execute(
            text(
                "INSERT INTO service_account_credentials "
                "(id, service_account_id, api_key_hash, created_by) "
                "VALUES (:id, :sa_id, :hash, 'system:test')"
            ),
            {"id": f"sac_{service_account_id}", "sa_id": service_account_id, "hash": api_key_hash},
        )
        await session.execute(
            text(
                "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, created_by) "
                "VALUES (:id, :actor_id, 'service_account', :scope, 'system:test')"
            ),
            {
                "id": f"asg_{service_account_id}",
                "actor_id": service_account_id,
                "scope": BROKER_EXECUTE_SCOPE,
            },
        )
        await session.commit()
    return plaintext


async def test_retired_key_resolves_to_service_account_identity(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    plaintext = await _seed_service_account(admin_db, service_account_id=_ACTIVE_SA)

    resolver = ApiKeyResolver(admin_db)
    identity = await resolver.resolve(plaintext)

    assert identity is not None
    assert identity.sub == _ACTIVE_SA
    assert identity.actor_type is ActorType.SERVICE_ACCOUNT
    assert identity.permissions == [BROKER_EXECUTE_SCOPE]
    assert identity.active is True


async def test_retired_key_resolve_logs_deprecation_warning(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """A successful jntc_live_ resolve is the operator's migration signal."""
    plaintext = await _seed_service_account(admin_db, service_account_id=_ACTIVE_SA)

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(plaintext)

    assert identity is not None
    warnings = [log for log in logs if log["event"] == "deprecated_toolkit_key_used"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["service_account_id"] == _ACTIVE_SA


async def test_disabled_service_account_resolves_to_none(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """A disabled successor account (revoked/deleted key) must not authenticate."""
    plaintext = await _seed_service_account(
        admin_db, service_account_id=_DISABLED_SA, status="disabled"
    )

    resolver = ApiKeyResolver(admin_db)
    assert await resolver.resolve(plaintext) is None


async def test_unknown_retired_key_resolves_to_none(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    await _seed_service_account(admin_db, service_account_id=_ACTIVE_SA)

    resolver = ApiKeyResolver(admin_db)
    assert await resolver.resolve("jntc_live_does_not_exist") is None
