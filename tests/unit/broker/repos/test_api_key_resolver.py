"""Unit tests for ApiKeyResolver."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.models import ActorType
from jentic_one.shared.telemetry.events import TelemetryEventName
from jentic_one.shared.telemetry.sink import TelemetrySink

Row = namedtuple("Row", ["scope"])
AgentRow = namedtuple("AgentRow", ["agent_id", "status", "owner_id"])
SARow = namedtuple("SARow", ["service_account_id", "status", "migrated_to_actor_id"])


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


@pytest.mark.asyncio
async def test_resolve_service_account_key_falls_back_after_agent_miss(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """Theme-8 Phase 1 (H-2): ``sak_`` tries the agent arm first; an unmigrated
    key misses it and resolves identically through the SA fallback."""
    sa_row = SARow(service_account_id="sva_456", status="active", migrated_to_actor_id=None)
    scope_rows = [Row(scope="broker:execute")]

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:  # agent arm — digest not in agent_credentials
            result.one_or_none.return_value = None
        elif call_count == 2:  # SA fallback
            result.one_or_none.return_value = sa_row
        else:  # permission load
            result.all.return_value = scope_rows
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await resolver.resolve("sak_test_secret_value")

    assert call_count == 3  # agent arm consulted first
    assert identity is not None
    assert identity.sub == "sva_456"
    assert identity.actor_type == ActorType.SERVICE_ACCOUNT
    assert identity.active is True
    assert "broker:execute" in identity.permissions


@pytest.mark.asyncio
async def test_migrated_sak_key_resolves_as_agent_first(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """A migrated key's digest lives in agent_credentials — the agent arm
    wins and the SA fallback is never consulted."""
    agent_row = AgentRow(agent_id="agnt_successor", status="active", owner_id="usr_owner")
    scope_rows = [Row(scope="broker:execute")]

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

    identity = await resolver.resolve("sak_migrated_key")

    assert call_count == 2  # agent lookup + permission load; no SA query
    assert identity is not None
    assert identity.sub == "agnt_successor"
    assert identity.actor_type == ActorType.AGENT


@pytest.mark.asyncio
async def test_sak_key_with_inactive_successor_fails_closed(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """H1: digest-hit on a DISABLED successor never falls back to the SA —
    disabling the successor agent is the operator's kill lever."""
    agent_row = AgentRow(agent_id="agnt_successor", status="disabled", owner_id="usr_owner")

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        result.one_or_none.return_value = agent_row
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("sak_disabled_successor")

    assert identity is None
    assert call_count == 1  # agent lookup only — the SA fallback is never consulted
    fail_closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(fail_closed) == 1
    assert fail_closed[0]["reason"] == "successor_inactive"


@pytest.mark.asyncio
async def test_stamped_sa_fallback_fails_closed(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """H1: an agent-arm digest MISS that lands on a STAMPED SA row refuses —
    a stamped SA is never a valid identity source (revoked-successor leg)."""
    sa_row = SARow(
        service_account_id="sva_456", status="active", migrated_to_actor_id="agnt_successor"
    )

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:  # agent arm — digest NULLed by revoke-api-key
            result.one_or_none.return_value = None
        else:  # SA fallback — row is stamped
            result.one_or_none.return_value = sa_row
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("sak_revoked_successor")

    assert identity is None
    assert call_count == 2  # agent lookup + SA row lookup; permissions never loaded
    fail_closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(fail_closed) == 1
    assert fail_closed[0]["reason"] == "stamped_service_account"
    assert fail_closed[0]["service_account_id"] == "sva_456"


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


def _fallback_session(admin_db: MagicMock) -> None:
    """Wire admin_db so any key misses the agent arm and hits the SA fallback."""
    sa_row = SARow(service_account_id="sva_456", status="active", migrated_to_actor_id=None)
    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = None
        elif call_count == 2:
            result.one_or_none.return_value = sa_row
        else:
            result.all.return_value = []
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr


@pytest.mark.asyncio
async def test_fallback_hit_records_telemetry_event(admin_db: MagicMock) -> None:
    """A fallback resolve bumps SERVICE_ACCOUNT_FALLBACK_RESOLVE on a real sink."""
    _fallback_session(admin_db)
    sink = TelemetrySink(enabled=True, queue_max=8)
    resolver = ApiKeyResolver(admin_db, telemetry=sink)

    identity = await resolver.resolve("sak_unmigrated")

    assert identity is not None
    event = sink.queue.get_nowait()
    assert event.name == TelemetryEventName.SERVICE_ACCOUNT_FALLBACK_RESOLVE
    assert event.actor_type == ActorType.SERVICE_ACCOUNT


@pytest.mark.asyncio
async def test_fallback_without_sink_does_not_crash(admin_db: MagicMock) -> None:
    """No telemetry sink (injected or active) → the fallback still resolves."""
    _fallback_session(admin_db)
    resolver = ApiKeyResolver(admin_db)

    identity = await resolver.resolve("jntc_live_unmigrated")

    assert identity is not None
    assert identity.actor_type == ActorType.SERVICE_ACCOUNT


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
