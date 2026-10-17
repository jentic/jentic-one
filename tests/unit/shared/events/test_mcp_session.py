"""Unit tests for ``mcp.session_started`` emission (lane D, issue #1177).

Covers the User-Agent parser, the session-id header validation, the layered
dedupe (the shared-state claim + events-table lookup + unique-index race
tolerance), and the shape of the emitted internal event vs. the closed-enum
telemetry tag.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import jentic_one.shared.events.mcp_session as mcp_session
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.events.mcp_session import (
    MCP_HTTP_WINDOW_SECONDS,
    McpUserAgent,
    mcp_http_window_key,
    parse_mcp_user_agent,
    record_mcp_session_started,
    schedule_mcp_http_session_emit,
    schedule_mcp_session_emit,
    valid_session_id_or_none,
)
from jentic_one.shared.models.events import EventSeverity, EventType, McpClient
from jentic_one.shared.state import MemoryStateBackend

#: The shared-state backend every ``_fake_ctx()`` of the running test shares,
#: standing in for one backend shared by every process of a deployment.
_BACKEND: list[MemoryStateBackend] = []


@pytest.fixture(autouse=True)
def backend() -> MemoryStateBackend:
    """A fresh shared-state backend per test."""
    _BACKEND[:] = [MemoryStateBackend()]
    return _BACKEND[0]


def _session_key(sid: str) -> str:
    return f"{mcp_session.SESSION_NAMESPACE}:{sid}"


async def _recorded(backend: MemoryStateBackend, sid: str) -> bool:
    return await backend.get(_session_key(sid)) == b"recorded"


def _fake_ctx(backend: MemoryStateBackend | None = None) -> MagicMock:
    """A Context on the test's shared-state backend whose admin_db.transaction()
    yields an AsyncMock session."""
    ctx = MagicMock()
    ctx.state_backend = backend if backend is not None else _BACKEND[0]
    session = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=session)
    transaction.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.transaction.return_value = transaction
    return ctx


# --- User-Agent parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("ua", "expected"),
    [
        (
            "jentic-mcp/1.2.3 (cursor/0.42.0)",
            McpUserAgent(server_version="1.2.3", client_name="cursor", client_version="0.42.0"),
        ),
        (
            "jentic-mcp/1.2.3 (Claude Desktop/2.1)",
            McpUserAgent(
                server_version="1.2.3", client_name="Claude Desktop", client_version="2.1"
            ),
        ),
        # Parenthetical is optional (clientInfo is a SHOULD) → client unknown.
        (
            "jentic-mcp/1.2.3",
            McpUserAgent(server_version="1.2.3", client_name=None, client_version=None),
        ),
        # Client without a version parses too.
        (
            "jentic-mcp/1.2.3 (codex)",
            McpUserAgent(server_version="1.2.3", client_name="codex", client_version=None),
        ),
        # Prefix match is case-insensitive, mirroring derive_origin.
        (
            "Jentic-MCP/2.0.0 (Cursor/1.0)",
            McpUserAgent(server_version="2.0.0", client_name="Cursor", client_version="1.0"),
        ),
    ],
)
def test_parse_mcp_user_agent_valid(ua: str, expected: McpUserAgent) -> None:
    assert parse_mcp_user_agent(ua) == expected


@pytest.mark.parametrize(
    "ua",
    [None, "", "jentic-cli/1.0.0", "Mozilla/5.0", "curl/8.0", "jentic-mcpX/1.0"],
)
def test_parse_mcp_user_agent_non_mcp(ua: str | None) -> None:
    """Non-MCP traffic parses to None — the fast path for every normal request."""
    assert parse_mcp_user_agent(ua) is None


def test_parse_mcp_user_agent_malformed_tail_degrades_to_unknown_client() -> None:
    """A jentic-mcp/ UA with a garbled tail still counts as MCP, client unknown."""
    parsed = parse_mcp_user_agent("jentic-mcp/1.0 (((broken")
    assert parsed is not None
    assert parsed.client_name is None
    assert McpClient.from_client_name(parsed.client_name) is McpClient.OTHER


def test_parse_mcp_user_agent_truncates_oversized_fields() -> None:
    """Untrusted UA fields are capped before they can reach persisted event data."""
    ua = f"jentic-mcp/{'v' * 300} ({'c' * 300}/{'x' * 300})"
    parsed = parse_mcp_user_agent(ua)
    assert parsed is not None
    assert parsed.server_version == "v" * 128
    assert parsed.client_name == "c" * 128
    assert parsed.client_version == "x" * 128


# --- Session-id validation -----------------------------------------------------


@pytest.mark.parametrize(
    "sid",
    ["sess-1", "a" * 128, "0123e4567-e89b:12d3", "run_7.retry"],
)
def test_valid_session_id_accepted(sid: str) -> None:
    assert valid_session_id_or_none(sid) == sid


@pytest.mark.parametrize(
    "sid",
    [None, "", "a" * 129, "bad session", "id\nnewline", "emoji🎉"],
)
def test_invalid_session_id_rejected(sid: str | None) -> None:
    """The header is untrusted input — anything outside the charset is ignored."""
    assert valid_session_id_or_none(sid) is None


# --- record_mcp_session_started ------------------------------------------------


@pytest.mark.asyncio
async def test_record_emits_event_with_full_data_and_closed_tag(
    backend: MemoryStateBackend,
) -> None:
    """First sighting of a session id emits with actor, data, and McpClient tag."""
    ctx = _fake_ctx()
    ua = McpUserAgent(server_version="1.2.3", client_name="Cursor IDE", client_version="0.42")

    with (
        patch(
            "jentic_one.shared.events.mcp_session.EventRepository.exists_with_data_value",
            AsyncMock(return_value=False),
        ) as exists,
        patch("jentic_one.shared.events.mcp_session.emit_event", new_callable=AsyncMock) as emit,
    ):
        await record_mcp_session_started(
            ctx, ua=ua, session_id="sess-1", actor_id="agnt_abc", actor_type="agent"
        )

    exists.assert_awaited_once()
    assert exists.call_args.kwargs["event_type"] == EventType.MCP_SESSION_STARTED
    assert exists.call_args.kwargs["key"] == "session_id"
    assert exists.call_args.kwargs["value"] == "sess-1"

    emit.assert_awaited_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["type"] == EventType.MCP_SESSION_STARTED
    assert kwargs["severity"] == EventSeverity.INFO
    # actor_id is mandatory here — it's what the per-agent UI read keys on.
    assert kwargs["actor_id"] == "agnt_abc"
    assert kwargs["actor_type"] == "agent"
    assert kwargs["data"] == {
        "session_id": "sess-1",
        "transport": "stdio",
        "client_name": "Cursor IDE",
        "client_version": "0.42",
    }
    # Telemetry side: at most the closed McpClient tag — never the raw string.
    assert kwargs["tags"] == {McpClient.CURSOR}

    assert await _recorded(backend, "sess-1")


@pytest.mark.asyncio
async def test_record_short_circuits_on_existing_table_row(backend: MemoryStateBackend) -> None:
    """A row already written (by another worker/plane) suppresses the emit."""
    ctx = _fake_ctx()
    ua = McpUserAgent(server_version="1.0", client_name=None, client_version=None)

    with (
        patch(
            "jentic_one.shared.events.mcp_session.EventRepository.exists_with_data_value",
            AsyncMock(return_value=True),
        ),
        patch("jentic_one.shared.events.mcp_session.emit_event", new_callable=AsyncMock) as emit,
    ):
        await record_mcp_session_started(
            ctx, ua=ua, session_id="sess-dup", actor_id="agnt_abc", actor_type="agent"
        )

    emit.assert_not_awaited()
    # The marker is primed so subsequent requests skip the table lookup too.
    assert await _recorded(backend, "sess-dup")


@pytest.mark.asyncio
async def test_record_failure_is_swallowed_and_retryable(backend: MemoryStateBackend) -> None:
    """A DB failure never propagates, and the session is not marked recorded (retry)."""
    ctx = _fake_ctx()
    ctx.admin_db.transaction.side_effect = RuntimeError("db down")
    ua = McpUserAgent(server_version="1.0", client_name=None, client_version=None)

    await record_mcp_session_started(
        ctx, ua=ua, session_id="sess-err", actor_id="agnt_abc", actor_type="agent"
    )

    assert not await _recorded(backend, "sess-err")


@pytest.mark.asyncio
async def test_record_lost_insert_race_treated_as_already_emitted(
    backend: MemoryStateBackend,
) -> None:
    """A unique-index IntegrityError means another worker won — skip, don't retry."""
    ctx = _fake_ctx()
    ua = McpUserAgent(server_version="1.0", client_name=None, client_version=None)

    with (
        patch(
            "jentic_one.shared.events.mcp_session.EventRepository.exists_with_data_value",
            AsyncMock(return_value=False),
        ),
        patch(
            "jentic_one.shared.events.mcp_session.emit_event",
            # transaction() maps the raw IntegrityError to this domain error.
            AsyncMock(side_effect=DatabaseIntegrityError("UNIQUE constraint failed")),
        ),
    ):
        await record_mcp_session_started(
            ctx, ua=ua, session_id="sess-race", actor_id="agnt_abc", actor_type="agent"
        )

    # The row exists (written by the race winner) — mark it so no process
    # sharing the backend re-schedules the emit for this session.
    assert await _recorded(backend, "sess-race")


# --- schedule_mcp_session_emit -------------------------------------------------


async def _schedule(
    ctx: Any,
    *,
    user_agent: str | None = "jentic-mcp/1.0 (cursor/1.0)",
    session_id: str | None = "sess-1",
) -> None:
    await schedule_mcp_session_emit(
        ctx,
        user_agent=user_agent,
        session_id=session_id,
        actor_id="agnt_abc",
        actor_type="agent",
    )


async def _run_scheduled_tasks() -> None:
    await asyncio.gather(*list(mcp_session._background_tasks))
    await asyncio.sleep(0)  # let the done callbacks run


@pytest.mark.asyncio
async def test_schedule_creates_task_for_mcp_request() -> None:
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule(_fake_ctx())
        await _run_scheduled_tasks()

    record.assert_awaited_once()
    assert record.call_args.kwargs["session_id"] == "sess-1"
    assert record.call_args.kwargs["actor_id"] == "agnt_abc"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_agent", "session_id"),
    [
        # Plain CLI calls carry the header too — the UA, not the header, gates.
        ("jentic-cli/1.0.0", "sess-1"),
        # Browser traffic.
        ("Mozilla/5.0", "sess-1"),
        # MCP UA without the session header: nothing to key the dedupe on.
        ("jentic-mcp/1.0 (cursor/1.0)", None),
        # MCP UA with a garbage header value.
        ("jentic-mcp/1.0 (cursor/1.0)", "bad session\n"),
    ],
)
async def test_schedule_ignores_non_qualifying_requests(
    user_agent: str | None, session_id: str | None, backend: MemoryStateBackend
) -> None:
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule(_fake_ctx(), user_agent=user_agent, session_id=session_id)
        await _run_scheduled_tasks()

    record.assert_not_awaited()
    # Non-qualifying traffic never touches the backend.
    assert await backend.get(_session_key("sess-1")) is None


@pytest.mark.asyncio
async def test_schedule_recorded_marker_short_circuits(backend: MemoryStateBackend) -> None:
    """Once a session id is recorded, no further task (or lookup) is scheduled."""
    await backend.set(_session_key("sess-1"), b"recorded", ttl_s=60)
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule(_fake_ctx())
        await _run_scheduled_tasks()

    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_concurrent_first_requests_emit_once() -> None:
    """Concurrent first requests for one session, on two processes, schedule one emit.

    Both "processes" are contexts over the one shared backend. The claim is
    taken before the task is created, so the duplicates no-op even while the
    first emit is still mid-transaction.
    """
    release = asyncio.Event()
    calls = 0

    async def slow_record(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        AsyncMock(side_effect=slow_record),
    ):
        await asyncio.gather(_schedule(_fake_ctx()), _schedule(_fake_ctx()))
        # A third request arriving while the emit is still in flight also skips.
        await asyncio.sleep(0)
        await _schedule(_fake_ctx())
        release.set()
        await _run_scheduled_tasks()

    assert calls == 1
    assert not mcp_session._background_tasks


@pytest.mark.asyncio
async def test_schedule_retries_after_failed_emit_once_the_claim_expires() -> None:
    """A failed emit leaves its claim to expire; a request after that retries."""
    now = [0.0]
    backend = MemoryStateBackend(clock=lambda: now[0])
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule(_fake_ctx(backend))
        await _run_scheduled_tasks()
        # The emit "failed" (the mock never marked it recorded): still claimed.
        await _schedule(_fake_ctx(backend))
        await _run_scheduled_tasks()
        assert record.await_count == 1

        now[0] = mcp_session._CLAIM_TTL_S
        await _schedule(_fake_ctx(backend))
        await _run_scheduled_tasks()

    assert record.await_count == 2


@pytest.mark.asyncio
async def test_schedule_without_ctx_is_a_noop() -> None:
    """Apps without a wired Context (some test harnesses) never crash the auth path."""
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule(None)

    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_claim_skips_the_emit_without_raising() -> None:
    """A full ``mcp-session`` namespace skips the emit; the request is unaffected."""
    backend = MemoryStateBackend(max_entries=1)
    await backend.set(_session_key("someone-else"), b"recorded", ttl_s=60)
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule(_fake_ctx(backend))
        await _run_scheduled_tasks()

    record.assert_not_awaited()


# --- HTTP windowed emit (phase-3 item 6) -----------------------------------------

_FROZEN_NOW = 1_700_000_000.0


def _freeze_time(now: float = _FROZEN_NOW) -> Any:
    return patch("jentic_one.shared.events.mcp_session.time.time", return_value=now)


def _patch_emit(*, exists: bool = False) -> tuple[Any, Any]:
    """Patchers for the record path: table lookup + emit_event."""
    return (
        patch(
            "jentic_one.shared.events.mcp_session.EventRepository.exists_with_data_value",
            AsyncMock(return_value=exists),
        ),
        patch("jentic_one.shared.events.mcp_session.emit_event", new_callable=AsyncMock),
    )


async def _schedule_http(
    ctx: Any,
    *,
    client_name: str | None = "cursor",
    client_version: str | None = "0.42",
    actor_id: str = "agnt_abc",
) -> None:
    await schedule_mcp_http_session_emit(
        ctx,
        client_name=client_name,
        client_version=client_version,
        actor_id=actor_id,
        actor_type="agent",
    )


def test_http_window_key_is_deterministic_and_axis_sensitive() -> None:
    """Same (agent x client x window) → same key; any axis change → new key."""
    base = mcp_http_window_key("agnt_a", "cursor", "1.0", now=_FROZEN_NOW)
    assert base == mcp_http_window_key("agnt_a", "cursor", "1.0", now=_FROZEN_NOW)
    # Within the same window, time does not change the key.
    assert base == mcp_http_window_key("agnt_a", "cursor", "1.0", now=_FROZEN_NOW + 1)
    assert base != mcp_http_window_key("agnt_b", "cursor", "1.0", now=_FROZEN_NOW)
    assert base != mcp_http_window_key("agnt_a", "claude", "1.0", now=_FROZEN_NOW)
    assert base != mcp_http_window_key("agnt_a", "cursor", "2.0", now=_FROZEN_NOW)
    assert base != mcp_http_window_key(
        "agnt_a", "cursor", "1.0", now=_FROZEN_NOW + MCP_HTTP_WINDOW_SECONDS
    )


def test_http_window_key_join_is_collision_free() -> None:
    """The name/version join is unambiguous: an embedded delimiter in one
    field can never make two distinct (name, version) pairs share a key."""
    assert mcp_http_window_key("agnt_a", "x\ny", "z", now=_FROZEN_NOW) != mcp_http_window_key(
        "agnt_a", "x", "y\nz", now=_FROZEN_NOW
    )
    # The length prefix itself cannot be spoofed by a crafted field value.
    assert mcp_http_window_key("agnt_a", "1:a", "b", now=_FROZEN_NOW) != mcp_http_window_key(
        "agnt_a", "1", ":ab", now=_FROZEN_NOW
    )


def test_http_window_key_case_folds_client_info() -> None:
    """The key case-folds like the McpClient tag: one row per client per
    window regardless of the case the client stamps its name/version with."""
    assert mcp_http_window_key("agnt_a", "Cursor", "1.0", now=_FROZEN_NOW) == mcp_http_window_key(
        "agnt_a", "cursor", "1.0", now=_FROZEN_NOW
    )
    # The agent axis is an opaque identifier — never folded.
    assert mcp_http_window_key("agnt_A", "cursor", "1.0", now=_FROZEN_NOW) != mcp_http_window_key(
        "agnt_a", "cursor", "1.0", now=_FROZEN_NOW
    )


@pytest.mark.asyncio
async def test_http_emit_carries_transport_and_windowed_session_id(
    backend: MemoryStateBackend,
) -> None:
    """The HTTP event: synthetic per-window session id, transport http, full
    clientInfo in data, the closed McpClient tag on the telemetry side."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()

    emit.assert_awaited_once()
    kwargs = emit.call_args.kwargs
    expected_sid = mcp_http_window_key("agnt_abc", "cursor", "0.42", now=_FROZEN_NOW)
    assert kwargs["type"] == EventType.MCP_SESSION_STARTED
    assert kwargs["actor_id"] == "agnt_abc"
    assert kwargs["actor_type"] == "agent"
    assert kwargs["data"] == {
        "session_id": expected_sid,
        "transport": "http",
        "client_name": "cursor",
        "client_version": "0.42",
    }
    assert kwargs["tags"] == {McpClient.CURSOR}
    assert await _recorded(backend, expected_sid)


@pytest.mark.asyncio
async def test_http_emits_once_within_window() -> None:
    """Repeat requests inside one window are a refused-claim no-op."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()
        await _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()

    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_reemits_after_window_rolls() -> None:
    """The same (agent x client) emits again once the window has passed."""
    exists_p, emit_p = _patch_emit()
    with exists_p, emit_p as emit:
        with _freeze_time(_FROZEN_NOW):
            await _schedule_http(_fake_ctx())
            await _run_scheduled_tasks()
        with _freeze_time(_FROZEN_NOW + MCP_HTTP_WINDOW_SECONDS):
            await _schedule_http(_fake_ctx())
            await _run_scheduled_tasks()

    assert emit.await_count == 2
    sids = [c.kwargs["data"]["session_id"] for c in emit.call_args_list]
    assert len(set(sids)) == 2


@pytest.mark.asyncio
async def test_http_distinct_agent_and_client_keys_do_not_collide() -> None:
    """One daemon serves many identities: each (agent x client) pair gets its
    own window key — never whoever sent the first request ever."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx(), actor_id="agnt_a", client_name="cursor")
        await _schedule_http(_fake_ctx(), actor_id="agnt_b", client_name="cursor")
        await _schedule_http(_fake_ctx(), actor_id="agnt_a", client_name="claude")
        await _run_scheduled_tasks()

    assert emit.await_count == 3
    sids = {c.kwargs["data"]["session_id"] for c in emit.call_args_list}
    assert len(sids) == 3
    actors = {c.kwargs["actor_id"] for c in emit.call_args_list}
    assert actors == {"agnt_a", "agnt_b"}


@pytest.mark.asyncio
async def test_http_absent_client_info_emits_unknown_client() -> None:
    """clientInfo is a SHOULD: absent still emits (mirroring stdio's
    client-unknown degrade) with OTHER on the wire and null in data — and
    dedupes on the same unknown-client key."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx(), client_name=None, client_version=None)
        await _run_scheduled_tasks()
        await _schedule_http(_fake_ctx(), client_name=None, client_version=None)
        await _run_scheduled_tasks()

    emit.assert_awaited_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["data"]["client_name"] is None
    assert kwargs["data"]["client_version"] is None
    assert kwargs["tags"] == {McpClient.OTHER}


@pytest.mark.asyncio
async def test_http_oversized_client_info_fields_are_capped() -> None:
    """_meta is untrusted input — persisted fields are capped like UA fields."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx(), client_name="c" * 300, client_version="v" * 300)
        await _run_scheduled_tasks()

    kwargs = emit.call_args.kwargs
    assert kwargs["data"]["client_name"] == "c" * 128
    assert kwargs["data"]["client_version"] == "v" * 128


@pytest.mark.asyncio
async def test_http_existing_table_row_suppresses_emit_and_primes_the_marker(
    backend: MemoryStateBackend,
) -> None:
    """Another worker already wrote this window's row → skip, mark recorded."""
    exists_p, emit_p = _patch_emit(exists=True)
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()

    emit.assert_not_awaited()
    sid = mcp_http_window_key("agnt_abc", "cursor", "0.42", now=_FROZEN_NOW)
    assert await _recorded(backend, sid)


@pytest.mark.asyncio
async def test_http_schedule_without_ctx_is_a_noop() -> None:
    with patch(
        "jentic_one.shared.events.mcp_session._record_session_started",
        new_callable=AsyncMock,
    ) as record:
        await _schedule_http(None)

    record.assert_not_awaited()
    assert not mcp_session._background_tasks


# --- per-agent distinct-key cap (review M1) --------------------------------------


@pytest.mark.asyncio
async def test_http_per_agent_distinct_key_cap_enforced() -> None:
    """An agent varying clientInfo per request mints at most the capped number
    of rows per window — beyond that, emission is skipped (and the row-per-
    request minting the review flagged is impossible)."""
    cap = mcp_session.MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        for i in range(cap + 10):
            await _schedule_http(_fake_ctx(), client_name=f"flood-client-{i}")
        await _run_scheduled_tasks()

    assert emit.await_count == cap
    # Already-admitted keys keep short-circuiting on their marker — the cap
    # never blocks a legitimately emitted client's repeats.
    with _freeze_time(), exists_p, emit_p as emit_again:
        await _schedule_http(_fake_ctx(), client_name="flood-client-0")
        await _run_scheduled_tasks()
    assert emit_again.await_count == 0


@pytest.mark.asyncio
async def test_http_cap_is_per_agent_not_global() -> None:
    """One flooding agent exhausting its cap leaves every other agent's
    emission untouched."""
    cap = mcp_session.MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        for i in range(cap + 5):
            await _schedule_http(_fake_ctx(), actor_id="agnt_flood", client_name=f"variant-{i}")
        await _schedule_http(_fake_ctx(), actor_id="agnt_ok", client_name="cursor")
        await _run_scheduled_tasks()

    actors = [c.kwargs["actor_id"] for c in emit.call_args_list]
    assert actors.count("agnt_flood") == cap
    assert actors.count("agnt_ok") == 1


@pytest.mark.asyncio
async def test_http_legit_multi_client_agent_under_cap_unaffected() -> None:
    """A real agent with a handful of distinct clients emits one row each —
    the cap only exists for pathological per-request variation."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        for name in ("cursor", "claude", "codex"):
            await _schedule_http(_fake_ctx(), client_name=name)
        await _run_scheduled_tasks()

    assert emit.await_count == 3


@pytest.mark.asyncio
async def test_http_cap_resets_on_the_next_window() -> None:
    """The cap is per (agent, window): a new UTC bucket admits the agent again."""
    cap = mcp_session.MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW
    exists_p, emit_p = _patch_emit()
    with exists_p, emit_p as emit:
        with _freeze_time(_FROZEN_NOW):
            for i in range(cap + 1):
                await _schedule_http(_fake_ctx(), client_name=f"variant-{i}")
            await _run_scheduled_tasks()
        with _freeze_time(_FROZEN_NOW + MCP_HTTP_WINDOW_SECONDS):
            await _schedule_http(_fake_ctx(), client_name="cursor")
            await _run_scheduled_tasks()

    assert emit.await_count == cap + 1


async def test_http_cap_logs_once_per_agent_window() -> None:
    """Crossing the cap logs a single warning; further skips are silent."""
    cap = mcp_session.MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW
    window = int(_FROZEN_NOW // MCP_HTTP_WINDOW_SECONDS)
    ctx = _fake_ctx()
    for _ in range(cap):
        assert await mcp_session._admit_agent_window_key(ctx, "agnt_abc", window)
    with patch.object(mcp_session.logger, "warning") as warn:
        assert not await mcp_session._admit_agent_window_key(ctx, "agnt_abc", window)
        assert not await mcp_session._admit_agent_window_key(ctx, "agnt_abc", window)
    warn.assert_called_once()


@pytest.mark.asyncio
async def test_http_cap_holds_across_processes_sharing_a_backend() -> None:
    """An agent spreading clientInfo variants over two processes still gets one cap."""
    cap = mcp_session.MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW
    process_a, process_b = _fake_ctx(), _fake_ctx()
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        for i in range(cap + 10):
            await _schedule_http(process_a if i % 2 else process_b, client_name=f"split-{i}")
        await _run_scheduled_tasks()

    assert emit.await_count == cap


@pytest.mark.asyncio
async def test_in_flight_cap_drops_new_emits_on_both_transports(
    backend: MemoryStateBackend,
) -> None:
    """At the in-flight cap new emits are dropped before they are claimed, so
    the key stays retryable once the backlog drains."""
    release = asyncio.Event()
    for _ in range(mcp_session._IN_FLIGHT_MAX):
        mcp_session._spawn_emit(release.wait())  # type: ignore[arg-type]

    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        await _schedule_http(_fake_ctx())
        with patch(
            "jentic_one.shared.events.mcp_session.record_mcp_session_started",
            new_callable=AsyncMock,
        ) as stdio_record:
            await _schedule(_fake_ctx())
        emit.assert_not_awaited()
        stdio_record.assert_not_awaited()
        assert await backend.get(_session_key("sess-1")) is None

        release.set()
        await _run_scheduled_tasks()
        assert not mcp_session._background_tasks

    # Nothing was claimed, so the key retries once the backlog drains.
    with _freeze_time(), exists_p, emit_p as emit_retry:
        await _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()
    emit_retry.assert_awaited_once()
