"""Integration tests for retired ``jntc_live_`` / ``sak_`` key authentication.

The theme-5 and theme-8 migrations copied each retired key's SHA-256 digest
onto its successor agent (``agent_credentials.api_key_hash``). A
``jntc_live_`` plaintext keeps resolving as that *agent* until the published
deprecation date (no earlier than 2026-12-01); a digest no agent holds, or an
inactive successor, fails closed. ``sak_`` keys stopped working in 0.41
(theme-8 Phase 4): they are refused even when a successor holds the digest.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator

import pytest
import structlog
from sqlalchemy import text

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

pytestmark = pytest.mark.integration


def _retired_toolkit_key() -> str:
    """A ``jntc_live_`` plaintext in the retired toolkit-key shape.

    The toolkit key generator died with the ``toolkit_keys`` table (Phase 6b);
    acceptance of already-migrated plaintexts survives until the published
    deprecation date, so the shape is fabricated here.
    """
    return f"jntc_live_{secrets.token_hex(16)}"


def _retired_service_account_key() -> str:
    """A ``sak_`` plaintext in the retired service-account key shape."""
    return f"sak_{secrets.token_hex(16)}"


_OWNER = "usr_rtka_owner"
_AGENT = "agnt_rtka_active"


@pytest.fixture()
async def clean_tables(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove the admin rows this module seeds, before and after."""

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id = :id"), {"id": _AGENT}
            )
            await session.execute(
                text("DELETE FROM agent_credentials WHERE agent_id = :id"), {"id": _AGENT}
            )
            await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": _AGENT})
            await session.execute(text("DELETE FROM users WHERE id = :owner"), {"owner": _OWNER})
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _seed_successor(
    admin_db: DatabaseSession, *, plaintext: str, status: str = "active"
) -> None:
    """Land the plaintext's digest on a successor agent holding the execute scope —
    what the theme-5 flatten / theme-8 migration wrote."""
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
                "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, :name, :owner, 'system:test', :status, 'system:test')"
            ),
            {"id": _AGENT, "name": f"toolkit-key:{_AGENT}", "owner": _OWNER, "status": status},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by) "
                "VALUES (:id, :agent_id, :hash, 'system:test')"
            ),
            {"id": f"agc_{_AGENT}", "agent_id": _AGENT, "hash": api_key_hash},
        )
        await session.execute(
            text(
                "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, created_by) "
                "VALUES (:id, :actor_id, 'agent', :scope, 'system:test')"
            ),
            {"id": f"asg_{_AGENT}", "actor_id": _AGENT, "scope": BROKER_EXECUTE_SCOPE},
        )
        await session.commit()


async def test_retired_toolkit_key_resolves_to_successor_agent(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """The unchanged ``jntc_live_`` plaintext resolves as the successor agent,
    and each resolve logs the deprecation WARNING naming that agent."""
    plaintext = _retired_toolkit_key()
    await _seed_successor(admin_db, plaintext=plaintext)

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(plaintext)

    assert identity is not None
    assert identity.sub == _AGENT
    assert identity.actor_type is ActorType.AGENT
    assert identity.permissions == [BROKER_EXECUTE_SCOPE]
    assert identity.active is True
    warnings = [log for log in logs if log["event"] == "deprecated_toolkit_key_used"]
    assert len(warnings) == 1 and warnings[0]["log_level"] == "warning"
    assert warnings[0]["agent_id"] == _AGENT


async def test_sak_key_is_refused_although_its_successor_holds_the_digest(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """0.41: a ``sak_`` key never authenticates, even as an active successor
    holding its digest; the INFO refusal names that agent for the operator."""
    plaintext = _retired_service_account_key()
    await _seed_successor(admin_db, plaintext=plaintext)

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        assert await resolver.resolve(plaintext) is None

    refused = [log for log in logs if log["event"] == "retired_service_account_key_refused"]
    assert len(refused) == 1 and refused[0]["log_level"] == "info"
    assert refused[0]["successor_agent_id"] == _AGENT


async def test_retired_key_with_inactive_successor_fails_closed(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """A suspended successor agent (the operator kill lever) must not authenticate."""
    plaintext = _retired_toolkit_key()
    await _seed_successor(admin_db, plaintext=plaintext, status="suspended")

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        assert await resolver.resolve(plaintext) is None
    closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(closed) == 1 and closed[0]["agent_id"] == _AGENT


async def test_unknown_retired_toolkit_key_resolves_to_none(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """No agent holds the digest (never migrated, or rotated): fail closed —
    there is no service-account fallback after theme-8 Phase 4."""
    await _seed_successor(admin_db, plaintext=_retired_toolkit_key())

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        assert await resolver.resolve("jntc_live_does_not_exist") is None
    assert [log for log in logs if log["event"] == "retired_key_unresolved"]
