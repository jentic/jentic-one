"""Unit tests for ApiKeyResolver."""

from __future__ import annotations

from collections import namedtuple
from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.models import ActorType

# Mirrors the resolver's agent-arm SELECT (#1543, #1944). ``name`` rides off the
# joined ``agents`` row, ``owner_active`` off the outer-joined owner row, and
# ``permission`` off the outer-joined ``actor_permission_grants`` row; all three
# default here so only the tests that care about them have to mention them.
AgentRow = namedtuple(
    "AgentRow",
    ["agent_id", "status", "owner_id", "name", "owner_active", "permission"],
    defaults=(None, True, None),
)


@pytest.fixture()
def admin_db() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def resolver(admin_db: MagicMock) -> ApiKeyResolver:
    return ApiKeyResolver(admin_db)


def _rows_db(
    admin_db: MagicMock, rows: Sequence[AgentRow], captured_sql: list[str] | None = None
) -> list[int]:
    """Every ``read_session`` query answers ``rows``; returns a one-element call counter."""
    calls = [0]
    session_mock = AsyncMock()

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        calls[0] += 1
        if captured_sql is not None:
            captured_sql.append(str(stmt))
        result = MagicMock()
        result.all.return_value = list(rows)
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.read_session.return_value = ctx_mgr
    return calls


@pytest.mark.asyncio
async def test_resolve_agent_key_active(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    rows = [
        AgentRow(
            agent_id="agnt_123",
            status="active",
            owner_id="usr_owner",
            name="billing-bot",
            permission="broker:execute",
        ),
        AgentRow(
            agent_id="agnt_123",
            status="active",
            owner_id="usr_owner",
            name="billing-bot",
            permission="toolkit:read",
        ),
    ]
    calls = _rows_db(admin_db, rows)

    identity = await resolver.resolve("jak_test_secret_value")

    assert calls[0] == 1
    admin_db.read_session.assert_called_once()
    admin_db.session.assert_not_called()
    assert identity is not None
    assert identity.sub == "agnt_123"
    assert identity.actor_type == ActorType.AGENT
    assert identity.parent_actor_id == "usr_owner"
    assert identity.active is True
    # Carried off the joined row, so audit summaries can name the agent (#1543).
    assert identity.actor_name == "billing-bot"
    assert identity.permissions == ["broker:execute", "toolkit:read"]


@pytest.mark.asyncio
async def test_resolve_agent_key_active_without_grants(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """An active agent with zero grants produces one LEFT JOIN row with ``permission=None``."""
    calls = _rows_db(
        admin_db,
        [
            AgentRow(
                agent_id="agnt_zero",
                status="active",
                owner_id="usr_owner",
                name="zero-grant-bot",
                permission=None,
            )
        ],
    )

    identity = await resolver.resolve("jak_zero_grant_secret")

    assert calls[0] == 1
    assert identity is not None
    assert identity.sub == "agnt_zero"
    assert identity.permissions == []


def _single_row_db(admin_db: MagicMock, row: AgentRow | None) -> list[int]:
    """Every query answers ``[row]`` (or ``[]`` when ``row`` is None)."""
    return _rows_db(admin_db, [] if row is None else [row])


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
    assert calls[0] == 1
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
    calls = _rows_db(admin_db, [])

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(raw_key)

    assert identity is None
    assert calls[0] == 1
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
    calls = _single_row_db(admin_db, agent_row)

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("jntc_live_migrated")

    assert calls[0] == 1
    assert identity is not None and identity.sub == "agnt_successor"
    deprecations = [log for log in logs if log["event"] == "deprecated_toolkit_key_used"]
    assert len(deprecations) == 1 and deprecations[0]["log_level"] == "warning"
    assert deprecations[0]["agent_id"] == "agnt_successor"


@pytest.mark.asyncio
async def test_resolve_agent_key_inactive(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    agent_row = AgentRow(agent_id="agnt_123", status="disabled", owner_id="usr_owner")
    _single_row_db(admin_db, agent_row)

    identity = await resolver.resolve("jak_disabled_agent")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_key_not_found(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    _rows_db(admin_db, [])

    identity = await resolver.resolve("jak_nonexistent_key")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_unknown_prefix(resolver: ApiKeyResolver) -> None:
    identity = await resolver.resolve("unknown_prefix_key")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_access_token_protocol(resolver: ApiKeyResolver, admin_db: MagicMock) -> None:
    """Verify resolve_access_token delegates to resolve (TokenResolverProtocol)."""
    _rows_db(admin_db, [])

    identity = await resolver.resolve_access_token("jak_proto_test")
    assert identity is None


@pytest.mark.asyncio
async def test_resolve_agent_key_deleted_owner_fails_closed(
    resolver: ApiKeyResolver, admin_db: MagicMock
) -> None:
    """An owned agent whose owner has ``deleted_at IS NOT NULL`` evaluates
    ``owner_active`` to ``False`` and fails closed."""
    agent_row = AgentRow(
        agent_id="agnt_123", status="active", owner_id="usr_deleted", owner_active=False
    )
    captured_sql: list[str] = []
    _rows_db(admin_db, [agent_row], captured_sql=captured_sql)

    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve("jak_deleted_owner_agent")

    assert identity is None
    assert len(captured_sql) == 1
    assert (
        "CASE WHEN u.deleted_at IS NULL THEN u.active ELSE FALSE END AS owner_active"
        in captured_sql[0]
    )
    assert "LEFT JOIN users u ON u.id = a.owner_id" in captured_sql[0]
    assert (
        "LEFT JOIN actor_permission_grants g ON g.actor_id = a.id AND g.actor_type = 'agent' WHERE"
        in captured_sql[0]
    )
    owner_inactive = [log for log in logs if log["event"] == "agent_owner_inactive"]
    assert len(owner_inactive) == 1
    assert owner_inactive[0]["agent_id"] == "agnt_123"
    assert owner_inactive[0]["owner_id"] == "usr_deleted"
