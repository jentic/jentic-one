"""Unit tests for ApiKeyResolver."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from sqlalchemy.exc import OperationalError, ProgrammingError

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.models import ActorType

Row = namedtuple("Row", ["permission"])
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
    permission_rows = [Row(permission="broker:execute"), Row(permission="toolkit:read")]

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = agent_row
        else:
            result.all.return_value = permission_rows
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


def _agent_then_grant_table_db(
    admin_db: MagicMock,
    *,
    agent_row: AgentRow,
    permission_rows_by_sql: dict[str, list[Row]],
    raise_on: dict[str, Exception] | None = None,
) -> list[dict[str, object]]:
    """Drive resolve(): first call returns ``agent_row``; later grant-table
    queries are answered (or raise) by matching a substring of the SQL text.

    Returns a running log of ``{"sql", "table"}`` for each grant query so a
    test can assert which grant table the resolver actually hit and in what
    order.
    """
    raise_on = raise_on or {}
    seen: list[dict[str, object]] = []
    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = agent_row
            return result
        sql = str(stmt)
        for needle, exc in raise_on.items():
            if needle in sql:
                seen.append({"sql": sql, "table": needle})
                raise exc
        for needle, rows in permission_rows_by_sql.items():
            if needle in sql:
                seen.append({"sql": sql, "table": needle})
                result.all.return_value = rows
                return result
        result.all.return_value = []
        return result

    def _session() -> AsyncMock:
        ctx_mgr = AsyncMock()
        ctx_mgr.__aenter__.return_value = session_mock
        ctx_mgr.__aexit__.return_value = None
        return ctx_mgr

    # ``_load_permissions`` opens a fresh session per grant-table attempt, so a
    # new context manager must be handed out on every call.
    admin_db.session.side_effect = _session
    session_mock.execute = _execute
    return seen


def _op_error(msg: str, *, pgcode: str | None = None) -> OperationalError:
    orig = Exception(msg)
    if pgcode is not None:
        orig.pgcode = pgcode  # type: ignore[attr-defined]
    return OperationalError(msg, {}, orig)


def _prog_error(msg: str, *, pgcode: str | None = None) -> ProgrammingError:
    orig = Exception(msg)
    if pgcode is not None:
        orig.pgcode = pgcode  # type: ignore[attr-defined]
    return ProgrammingError(msg, {}, orig)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_table_error",
    [
        _op_error("no such table: actor_permission_grants"),  # sqlite
        _prog_error(
            'relation "actor_permission_grants" does not exist', pgcode="42P01"
        ),  # postgres (SQLSTATE 42P01)
    ],
    ids=["sqlite-OperationalError", "postgres-ProgrammingError"],
)
async def test_load_permissions_falls_back_to_legacy_grant_table(
    resolver: ApiKeyResolver, admin_db: MagicMock, missing_table_error: Exception
) -> None:
    """During the rolling-upgrade window after the service-account drop but
    before the tail rename, ``actor_permission_grants`` does not exist yet.
    The resolver must fall back to ``actor_scope_grants`` (SELECT ``scope``) so
    key auth keeps serving — on both dialects' missing-table errors. The
    fallback must never widen: it returns exactly the stored grants."""
    agent_row = AgentRow(agent_id="agnt_live", status="active", owner_id="usr_owner")
    seen = _agent_then_grant_table_db(
        admin_db,
        agent_row=agent_row,
        permission_rows_by_sql={"actor_scope_grants": [Row(permission="capabilities:read")]},
        raise_on={"actor_permission_grants": missing_table_error},
    )

    identity = await resolver.resolve("jak_rolling_upgrade")

    assert identity is not None and identity.sub == "agnt_live"
    # Exactly the stored legacy grant — no widening, no default injection.
    assert identity.permissions == ["capabilities:read"]
    # Order proves intent: new table first, then the legacy fallback.
    assert [s["table"] for s in seen] == ["actor_permission_grants", "actor_scope_grants"]


@pytest.mark.asyncio
async def test_load_permissions_fails_closed_when_both_grant_tables_missing(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """If neither grant table can be read, the resolver yields an empty
    permission set (fail-closed) rather than raising or elevating — the agent
    authenticates but can do nothing until the schema settles."""
    agent_row = AgentRow(agent_id="agnt_live", status="active", owner_id="usr_owner")
    _agent_then_grant_table_db(
        admin_db,
        agent_row=agent_row,
        permission_rows_by_sql={},
        raise_on={
            "actor_permission_grants": _op_error("no such table: actor_permission_grants"),
            "actor_scope_grants": _op_error("no such table: actor_scope_grants"),
        },
    )

    identity = await resolver.resolve("jak_both_tables_gone")

    assert identity is not None and identity.sub == "agnt_live"
    assert identity.permissions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transient_error",
    [
        _op_error("database is locked"),  # sqlite lock — no pgcode, not a missing table
        _op_error("connection timed out"),  # dropped/slow connection
        _prog_error("syntax error at or near", pgcode="42601"),  # genuine query bug
        _op_error("deadlock detected", pgcode="40P01"),  # postgres deadlock
    ],
    ids=["sqlite-locked", "connection-timeout", "syntax-error", "pg-deadlock"],
)
async def test_load_permissions_reraises_non_missing_table_errors(
    resolver: ApiKeyResolver, admin_db: MagicMock, transient_error: Exception
) -> None:
    """A transient/real DB fault must PROPAGATE, never be swallowed into an
    empty permission set. Swallowing a lock, a dropped connection, or a syntax
    bug would silently strip an authenticated agent's permissions to [] — a
    silent authz drop. Only a genuinely-missing grant table is tolerated."""
    agent_row = AgentRow(agent_id="agnt_live", status="active", owner_id="usr_owner")
    _agent_then_grant_table_db(
        admin_db,
        agent_row=agent_row,
        permission_rows_by_sql={},
        raise_on={"actor_permission_grants": transient_error},
    )

    with pytest.raises((OperationalError, ProgrammingError)):
        await resolver.resolve("jak_transient_db_fault")


@pytest.mark.asyncio
async def test_load_permissions_reraises_when_legacy_table_read_errors(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """The new table is genuinely missing (tolerated → fall back), but the
    legacy read then hits a lock. That lock must propagate too — the fallback
    path is not a license to swallow a real fault on the second table."""
    agent_row = AgentRow(agent_id="agnt_live", status="active", owner_id="usr_owner")
    _agent_then_grant_table_db(
        admin_db,
        agent_row=agent_row,
        permission_rows_by_sql={},
        raise_on={
            "actor_permission_grants": _op_error("no such table: actor_permission_grants"),
            "actor_scope_grants": _op_error("database is locked"),
        },
    )

    with pytest.raises(OperationalError, match="database is locked"):
        await resolver.resolve("jak_legacy_locked")


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
