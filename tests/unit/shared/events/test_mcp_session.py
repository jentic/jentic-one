"""Unit tests for ``mcp.session_started`` emission (lane D, issue #1177).

Covers the User-Agent parser, the session-id header validation, the layered
dedupe (synchronous in-flight/seen-set short-circuits + events-table lookup +
unique-index race tolerance), and the shape of the emitted internal event vs.
the closed-enum telemetry tag.
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


@pytest.fixture(autouse=True)
def _clear_seen_sessions():
    """Isolate the module-level dedupe state per test."""
    mcp_session._seen_sessions.clear()
    mcp_session._in_flight.clear()
    yield
    mcp_session._seen_sessions.clear()
    mcp_session._in_flight.clear()


def _fake_ctx() -> MagicMock:
    """A Context whose admin_db.transaction() yields an AsyncMock session."""
    ctx = MagicMock()
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
async def test_record_emits_event_with_full_data_and_closed_tag() -> None:
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

    assert "sess-1" in mcp_session._seen_sessions


@pytest.mark.asyncio
async def test_record_short_circuits_on_existing_table_row() -> None:
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
    # The seen-set is primed so subsequent requests skip the table lookup too.
    assert "sess-dup" in mcp_session._seen_sessions


@pytest.mark.asyncio
async def test_record_failure_is_swallowed_and_retryable() -> None:
    """A DB failure never propagates, and the session stays unremembered (retry)."""
    ctx = _fake_ctx()
    ctx.admin_db.transaction.side_effect = RuntimeError("db down")
    ua = McpUserAgent(server_version="1.0", client_name=None, client_version=None)

    await record_mcp_session_started(
        ctx, ua=ua, session_id="sess-err", actor_id="agnt_abc", actor_type="agent"
    )

    assert "sess-err" not in mcp_session._seen_sessions


@pytest.mark.asyncio
async def test_record_lost_insert_race_treated_as_already_emitted() -> None:
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

    # The row exists (written by the race winner) — remember so this process
    # never re-schedules the emit for this session.
    assert "sess-race" in mcp_session._seen_sessions


# --- schedule_mcp_session_emit -------------------------------------------------


def _schedule(
    ctx: Any,
    *,
    user_agent: str | None = "jentic-mcp/1.0 (cursor/1.0)",
    session_id: str | None = "sess-1",
) -> None:
    schedule_mcp_session_emit(
        ctx,
        user_agent=user_agent,
        session_id=session_id,
        actor_id="agnt_abc",
        actor_type="agent",
    )


@pytest.mark.asyncio
async def test_schedule_creates_task_for_mcp_request() -> None:
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        _schedule(_fake_ctx())
        # Let the created task run.
        for task in list(mcp_session._background_tasks):
            await task

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
    user_agent: str | None, session_id: str | None
) -> None:
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        _schedule(_fake_ctx(), user_agent=user_agent, session_id=session_id)
        for task in list(mcp_session._background_tasks):
            await task

    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_seen_set_short_circuits() -> None:
    """Once a session id is remembered, no further task (or lookup) is scheduled."""
    mcp_session._seen_sessions.add("sess-1")
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        _schedule(_fake_ctx())
        for task in list(mcp_session._background_tasks):
            await task

    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_concurrent_first_requests_emit_once() -> None:
    """Two concurrent first requests for one session schedule exactly one emit.

    Reproduces the reviewed race: before the in-flight set, both schedules
    passed the seen-set check (it is only populated after the background task
    commits) and both created tasks. The in-flight check-and-add is synchronous
    in schedule_mcp_session_emit, so the second schedule must no-op even while
    the first task is still mid-transaction.
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
        _schedule(_fake_ctx())
        _schedule(_fake_ctx())  # concurrent duplicate — same session id
        assert "sess-1" in mcp_session._in_flight
        # A third request arriving while the emit is still in flight also skips.
        await asyncio.sleep(0)
        _schedule(_fake_ctx())
        release.set()
        for task in list(mcp_session._background_tasks):
            await task
        # Let the done callbacks run.
        await asyncio.sleep(0)

    assert calls == 1
    assert "sess-1" not in mcp_session._in_flight
    assert not mcp_session._background_tasks


@pytest.mark.asyncio
async def test_schedule_retries_after_failed_emit() -> None:
    """A failed emit clears the in-flight entry so the next request retries."""
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        _schedule(_fake_ctx())
        for task in list(mcp_session._background_tasks):
            await task
        await asyncio.sleep(0)

        # The emit failed (record swallows errors and does not touch the
        # seen-set), so a later request for the same session schedules again.
        assert "sess-1" not in mcp_session._in_flight
        _schedule(_fake_ctx())
        for task in list(mcp_session._background_tasks):
            await task

    assert record.await_count == 2


@pytest.mark.asyncio
async def test_schedule_without_ctx_is_a_noop() -> None:
    """Apps without a wired Context (some test harnesses) never crash the auth path."""
    with patch(
        "jentic_one.shared.events.mcp_session.record_mcp_session_started",
        new_callable=AsyncMock,
    ) as record:
        _schedule(None)

    record.assert_not_awaited()


def test_seen_set_is_bounded() -> None:
    """The per-process seen-set can never grow past its cap."""
    for i in range(mcp_session._SEEN_SESSIONS_MAX):
        mcp_session._remember(f"sess-{i}")
    assert len(mcp_session._seen_sessions) == mcp_session._SEEN_SESSIONS_MAX
    mcp_session._remember("one-more")
    assert len(mcp_session._seen_sessions) == 1
    assert "one-more" in mcp_session._seen_sessions


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


async def _run_scheduled_tasks() -> None:
    for task in list(mcp_session._background_tasks):
        await task
    await asyncio.sleep(0)  # let the done callbacks run


def _schedule_http(
    ctx: Any,
    *,
    client_name: str | None = "cursor",
    client_version: str | None = "0.42",
    actor_id: str = "agnt_abc",
) -> None:
    schedule_mcp_http_session_emit(
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


@pytest.mark.asyncio
async def test_http_emit_carries_transport_and_windowed_session_id() -> None:
    """The HTTP event: synthetic per-window session id, transport http, full
    clientInfo in data, the closed McpClient tag on the telemetry side."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        _schedule_http(_fake_ctx())
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
    assert expected_sid in mcp_session._seen_sessions


@pytest.mark.asyncio
async def test_http_emits_once_within_window() -> None:
    """Repeat requests inside one window are a set-membership no-op."""
    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p as emit:
        _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()
        _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()

    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_reemits_after_window_rolls() -> None:
    """The same (agent x client) emits again once the window has passed."""
    exists_p, emit_p = _patch_emit()
    with exists_p, emit_p as emit:
        with _freeze_time(_FROZEN_NOW):
            _schedule_http(_fake_ctx())
            await _run_scheduled_tasks()
        with _freeze_time(_FROZEN_NOW + MCP_HTTP_WINDOW_SECONDS):
            _schedule_http(_fake_ctx())
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
        _schedule_http(_fake_ctx(), actor_id="agnt_a", client_name="cursor")
        _schedule_http(_fake_ctx(), actor_id="agnt_b", client_name="cursor")
        _schedule_http(_fake_ctx(), actor_id="agnt_a", client_name="claude")
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
        _schedule_http(_fake_ctx(), client_name=None, client_version=None)
        await _run_scheduled_tasks()
        _schedule_http(_fake_ctx(), client_name=None, client_version=None)
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
        _schedule_http(_fake_ctx(), client_name="c" * 300, client_version="v" * 300)
        await _run_scheduled_tasks()

    kwargs = emit.call_args.kwargs
    assert kwargs["data"]["client_name"] == "c" * 128
    assert kwargs["data"]["client_version"] == "v" * 128


@pytest.mark.asyncio
async def test_http_existing_table_row_suppresses_emit_and_primes_seen() -> None:
    """Another worker already wrote this window's row → skip, remember."""
    exists_p, emit_p = _patch_emit(exists=True)
    with _freeze_time(), exists_p, emit_p as emit:
        _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()

    emit.assert_not_awaited()
    sid = mcp_http_window_key("agnt_abc", "cursor", "0.42", now=_FROZEN_NOW)
    assert sid in mcp_session._seen_sessions


@pytest.mark.asyncio
async def test_http_schedule_without_ctx_is_a_noop() -> None:
    with patch(
        "jentic_one.shared.events.mcp_session._record_session_started",
        new_callable=AsyncMock,
    ) as record:
        _schedule_http(None)

    record.assert_not_awaited()
    assert not mcp_session._background_tasks


@pytest.mark.asyncio
async def test_http_window_keys_ride_the_bounded_seen_set() -> None:
    """Long-lived daemon, many identities: the window keys land in the same
    capped seen-set as stdio session ids — memory stays bounded."""
    for i in range(mcp_session._SEEN_SESSIONS_MAX):
        mcp_session._remember(f"filler-{i}")

    exists_p, emit_p = _patch_emit()
    with _freeze_time(), exists_p, emit_p:
        _schedule_http(_fake_ctx())
        await _run_scheduled_tasks()

    # _remember cleared the full set before adding, so growth is impossible.
    assert len(mcp_session._seen_sessions) == 1
    assert mcp_http_window_key("agnt_abc", "cursor", "0.42", now=_FROZEN_NOW) in (
        mcp_session._seen_sessions
    )
