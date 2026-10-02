"""Unit tests for ApiKeyResolver."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.models import ActorType

Row = namedtuple("Row", ["scope"])
AgentRow = namedtuple("AgentRow", ["agent_id", "status", "owner_id"])


@pytest.fixture()
def admin_db() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def resolver(admin_db: MagicMock) -> ApiKeyResolver:
    return ApiKeyResolver(admin_db)


@pytest.mark.asyncio
async def test_resolve_agent_key_active(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    agent_row = AgentRow(agent_id="agnt_123", status="active", owner_id="usr_owner")
    scope_rows = [Row(scope="broker:execute"), Row(scope="toolkit:read")]

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = agent_row
        else:
            result.all.return_value = scope_rows
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await resolver.resolve("jak_test_secret_value")

    assert identity is not None
    assert identity.sub == "agnt_123"
    assert identity.actor_type == ActorType.AGENT
    assert identity.parent_actor_id == "usr_owner"
    assert identity.active is True
    assert "broker:execute" in identity.permissions
    assert "toolkit:read" in identity.permissions


def _single_row_db(admin_db: MagicMock, row: AgentRow | None) -> list[int]:
    """Every query answers ``row``; returns a one-element call counter."""
    calls = [0]
    session_mock = AsyncMock()

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        calls[0] += 1
        result = MagicMock()
        result.one_or_none.return_value = row
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "successor"),
    [
        (AgentRow(agent_id="agnt_successor", status="active", owner_id="usr_o"), "agnt_successor"),
        (AgentRow(agent_id="agnt_off", status="disabled", owner_id="usr_o"), "agnt_off"),
        (None, None),
    ],
    ids=["active-successor", "inactive-successor", "no-successor"],
)
async def test_sak_key_is_refused_even_when_a_successor_holds_its_digest(
    resolver: ApiKeyResolver, admin_db: MagicMock, row: AgentRow | None, successor: str | None
) -> None:
    """Theme-8 Phase 4 (0.41): ``sak_`` keys stop working. Even a digest the
    migration copied onto an active successor never resolves; one INFO line
    names the successor (for the operator) and the jak_ next step."""
    calls = _single_row_db(admin_db, row)

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("sak_migrated_key")

    assert identity is None
    assert calls[0] == 1  # the successor lookup only — no permission load
    refused = [log for log in logs if log["event"] == "retired_service_account_key_refused"]
    assert len(refused) == 1 and refused[0]["log_level"] == "info"
    assert refused[0]["successor_agent_id"] == successor
    assert "jak_" in refused[0]["actionable_step"]
    assert not [log for log in logs if log["log_level"] in ("warning", "error")]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_key", ["jntc_live_unmigrated"])
async def test_retired_key_digest_miss_fails_closed(
    resolver: ApiKeyResolver, admin_db: MagicMock, raw_key: str
) -> None:
    """Theme-8 Phase 4: no service-account fallback remains — a retired key
    no agent carries resolves to None after one agent lookup, with a WARNING."""
    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        result.one_or_none.return_value = None
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(raw_key)

    assert identity is None
    assert call_count == 1
    unresolved = [log for log in logs if log["event"] == "retired_key_unresolved"]
    assert len(unresolved) == 1 and unresolved[0]["log_level"] == "info"


@pytest.mark.asyncio
async def test_jntc_key_with_inactive_successor_fails_closed(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """H1: a digest hit on a DISABLED successor fails closed — disabling the
    successor agent is the operator's kill lever."""
    calls = _single_row_db(
        admin_db, AgentRow(agent_id="agnt_successor", status="disabled", owner_id="usr_owner")
    )

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("jntc_live_disabled_successor")

    assert identity is None
    assert calls[0] == 1  # agent lookup only
    fail_closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(fail_closed) == 1
    assert fail_closed[0]["reason"] == "successor_inactive"
    assert fail_closed[0]["agent_id"] == "agnt_successor"  # names the kill lever


@pytest.mark.asyncio
async def test_migrated_jntc_key_logs_deprecation_on_agent_arm(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """M3: the theme-5 6b signal must not go dark after migration — a
    ``jntc_live_`` resolve served by the AGENT arm still WARNs, naming the
    successor."""
    agent_row = AgentRow(agent_id="agnt_successor", status="active", owner_id="usr_owner")

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = agent_row
        else:
            result.all.return_value = []
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("jntc_live_migrated")

    assert identity is not None and identity.sub == "agnt_successor"
    deprecations = [log for log in logs if log["event"] == "deprecated_toolkit_key_used"]
    assert len(deprecations) == 1 and deprecations[0]["log_level"] == "warning"
    assert deprecations[0]["agent_id"] == "agnt_successor"


@pytest.mark.asyncio
async def test_resolve_agent_key_inactive(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    agent_row = AgentRow(agent_id="agnt_123", status="disabled", owner_id="usr_owner")

    session_mock = AsyncMock()

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        result = MagicMock()
        result.one_or_none.return_value = agent_row
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await resolver.resolve("jak_disabled_agent")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_key_not_found(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    session_mock = AsyncMock()

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        result = MagicMock()
        result.one_or_none.return_value = None
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await resolver.resolve("jak_nonexistent_key")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_unknown_prefix(resolver: ApiKeyResolver) -> None:
    identity = await resolver.resolve("unknown_prefix_key")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_access_token_protocol(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    """Verify resolve_access_token delegates to resolve (TokenResolverProtocol)."""
    session_mock = AsyncMock()

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        result = MagicMock()
        result.one_or_none.return_value = None
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await resolver.resolve_access_token("jak_proto_test")
    assert identity is None
