"""``mcp.session_started`` emission — first authenticated request per MCP session.

Two transports emit this event, sharing one dedupe machine (the seen/in-flight
sets, the events-table lookup, and the partial unique index on
``(type, data->>'session_id')``) but with different **keys**:

- **stdio** (lane D, issue #1177): once per session UUID relayed by the Go
  server — :func:`schedule_mcp_session_emit`, described below.
- **HTTP** (phase-3 item 6): the daemon-native ``/mcp`` mount sees MCP
  protocol traffic directly, and under spec 2026-07-28 there is no
  ``initialize`` and no session id — clientInfo optionally rides each
  request's ``_meta``. One long-lived daemon serves many identities, so the
  emit key is once per (agent identity x clientInfo name/version) per
  **window** — :func:`schedule_mcp_http_session_emit`.

Local-MCP lane D (issue #1177). The stdio MCP server (Go, ``jentic mcp``)
terminates all MCP protocol traffic; the backend only ever sees plain HTTP
calls. Two request properties identify an MCP session:

- ``User-Agent: jentic-mcp/<version> (<client>/<clientversion>)`` — the UA
  prefix (not the header below) is what marks the traffic as MCP; the
  parenthetical clientInfo is optional (a SHOULD in the MCP spec), so an
  absent clientInfo means "client unknown".
- ``X-Jentic-Session-Id: <session-uuid>`` — the shipped correlation seam. An
  orchestrator-set ``$JENTIC_SESSION_ID`` groups several server processes into
  one logical session; the Go server's per-process UUID is the fallback. Plain
  CLI calls carry this header too — which is why the UA gates.

The event is emitted **once per session id**, on the first authenticated
request that shows both, from **both planes** (the control plane's
``resolve_identity`` and the broker's ``require_broker_identity`` — a session
whose only traffic is ``execute`` never touches the control plane). Dedupe is
three-layered (the lane-D plan's two layers, hardened against races): a
synchronous per-process short-circuit (the seen-set plus an in-flight set
checked before any task is scheduled, so concurrent first requests on one
worker schedule at most one emit), one indexed events-table lookup on
``data.session_id`` (the table is the store shared across workers/services),
and — because the lookup is check-then-insert and two workers can race past
it — a partial unique index on ``(type, data->>'session_id')`` scoped to this
event type (``uq_events_mcp_session_started_session``). The loser of a
cross-worker race gets an ``IntegrityError`` and skips, so the store holds
exactly one row per session id. Emission is fire-and-forget in a
background task so the auth path never waits on it, and it rides
``emit_event``'s existing consent gate — the telemetry wire carries at most
the closed ``McpClient`` tag, while the internal event's ``data`` holds the
full clientInfo, the transport, and the session id for the UI (two-plane
pattern).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError

from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.events import emit_event
from jentic_one.shared.models.events import EventSeverity, EventType, McpClient

logger = structlog.get_logger(__name__)

MCP_USER_AGENT_PREFIX = "jentic-mcp/"
SESSION_ID_HEADER = "x-jentic-session-id"

#: Mirrors the Go client's ``SanitizeSessionID`` charset + length bound
#: (``cli/client/client.go``) — but the header is untrusted input, so the
#: backend re-validates rather than assuming the sender sanitised.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

#: ``jentic-mcp/<version>`` optionally followed by ``(<client>/<clientversion>)``.
#: The client version segment is itself optional (``(cursor)`` parses too).
_MCP_UA_RE = re.compile(
    r"^jentic-mcp/(?P<version>\S+)"
    r"(?:\s+\((?P<client>[^/()]+?)(?:/(?P<client_version>[^()]*))?\))?\s*$",
    re.IGNORECASE,
)

#: Transport for the header-seam path. Phase-3's mounted HTTP app emits with
#: its own transport value and windowed dedupe key — not through this module's
#: once-per-session-id path.
_STDIO_TRANSPORT = "stdio"

#: Transport for the mounted ``/mcp`` app's windowed emit path.
_HTTP_TRANSPORT = "http"

#: The HTTP emit window (phase-3 item 6): at most one ``mcp.session_started``
#: per (agent identity x clientInfo name/version) per **6-hour, UTC-aligned
#: window**. Decision + rationale (recorded in this PR, per the phase doc):
#:
#: - The 2-E2 per-agent sessions list is the consumer. Six hours splits a day
#:   into at most four rows per (agent x client) — enough granularity to see
#:   "came back this afternoon" reconnects without a busy agent spamming a
#:   row per request (1h would allow 24/day) or a whole day collapsing into
#:   one row that hides every reconnect (24h).
#: - The stdio twin emits once per server process; desktop runtimes restart
#:   the server a handful of times a working day, so hours-scale windows keep
#:   the two transports' row volumes comparable in the same list.
#: - Windows are **fixed UTC buckets** (``epoch // window``), not sliding
#:   relative to the last emit: the bucket makes the dedupe key deterministic
#:   across workers, so the existing partial unique index on
#:   ``(type, data->>'session_id')`` enforces exactly-once per window across
#:   the whole deployment — a sliding window cannot be expressed as a unique
#:   key and would multiply events by worker/replica count. Worst case a
#:   client reconnecting exactly across a bucket boundary yields two rows
#:   minutes apart; a multi-worker deployment duplicating every window would
#:   be strictly worse for the sessions list.
#:
#: A code constant, deliberately not config: the value tunes a UI list's
#: granularity, not deployment behaviour.
MCP_HTTP_WINDOW_SECONDS = 6 * 60 * 60

#: Longest UA-derived field persisted to event ``data``. The User-Agent is
#: untrusted input; without a cap a hostile client could grow every
#: ``mcp.session_started`` row by kilobytes of junk.
_UA_FIELD_MAX = 128

#: Per-process short-circuit: session ids already confirmed emitted (here or
#: by another worker, via the table lookup). Bounded so a long-lived process
#: fed unique ids can't grow it without limit — clearing only costs one extra
#: table lookup per session, never a duplicate event.
_seen_sessions: set[str] = set()
_SEEN_SESSIONS_MAX = 4096

#: Session ids with an emit task currently in flight on this process. Checked
#: and added *synchronously* in :func:`schedule_mcp_session_emit`, before the
#: task is created, so two concurrent first requests on one worker can never
#: both schedule an emit. Entries are discarded in the task's done callback —
#: on failure the session is not in the seen-set either, so the next request
#: retries.
_in_flight: set[str] = set()

_background_tasks: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class McpUserAgent:
    """Parsed ``jentic-mcp/…`` User-Agent."""

    server_version: str
    client_name: str | None
    client_version: str | None


def parse_mcp_user_agent(user_agent: str | None) -> McpUserAgent | None:
    """Parse a ``jentic-mcp/<version> (<client>/<clientversion>)`` User-Agent.

    Returns ``None`` for anything that is not MCP-server traffic. A UA with the
    prefix but a malformed tail still identifies MCP traffic — it degrades to
    "client unknown" rather than dropping the session event. Parsed fields are
    capped at ``_UA_FIELD_MAX`` chars — they end up persisted in event ``data``.
    """
    if not user_agent or not user_agent.lower().startswith(MCP_USER_AGENT_PREFIX):
        return None
    match = _MCP_UA_RE.match(user_agent.strip())
    if match is None:
        return McpUserAgent(server_version="", client_name=None, client_version=None)
    client = match.group("client")
    client_version = match.group("client_version")
    return McpUserAgent(
        server_version=match.group("version")[:_UA_FIELD_MAX],
        client_name=client.strip()[:_UA_FIELD_MAX] if client else None,
        client_version=client_version.strip()[:_UA_FIELD_MAX] if client_version else None,
    )


def valid_session_id_or_none(session_id: str | None) -> str | None:
    """Return the header value when it is a plausible session id, else ``None``."""
    if session_id is not None and _SESSION_ID_RE.match(session_id):
        return session_id
    return None


def _remember(session_id: str) -> None:
    if len(_seen_sessions) >= _SEEN_SESSIONS_MAX:
        _seen_sessions.clear()
    _seen_sessions.add(session_id)


async def record_mcp_session_started(
    ctx: Context,
    *,
    ua: McpUserAgent,
    session_id: str,
    actor_id: str,
    actor_type: str,
) -> None:
    """Emit ``mcp.session_started`` for ``session_id`` unless already emitted.

    Opens its own admin-DB transaction (callers run this off the request path).
    The table lookup short-circuits sessions already recorded by another
    process; the once-per-session guarantee itself is held by the partial
    unique index ``uq_events_mcp_session_started_session`` — losing a
    cross-worker check-then-insert race raises ``IntegrityError``, which is
    treated as "already emitted". Other failures are logged and the seen-set
    is left untouched so the next request for the session retries.
    """
    await _record_session_started(
        ctx,
        session_id=session_id,
        transport=_STDIO_TRANSPORT,
        client_name=ua.client_name,
        client_version=ua.client_version,
        actor_id=actor_id,
        actor_type=actor_type,
    )


async def _record_session_started(
    ctx: Context,
    *,
    session_id: str,
    transport: str,
    client_name: str | None,
    client_version: str | None,
    actor_id: str,
    actor_type: str,
) -> None:
    """The transport-agnostic emit body shared by the stdio and HTTP paths.

    ``session_id`` is whatever the transport's dedupe keys on — the relayed
    session UUID for stdio, the synthetic per-window key for HTTP. Everything
    else (table lookup, unique-index race tolerance, seen-set priming) is
    identical.
    """
    try:
        async with ctx.admin_db.transaction() as session:
            already_emitted = await EventRepository.exists_with_data_value(
                session,
                event_type=EventType.MCP_SESSION_STARTED,
                key="session_id",
                value=session_id,
            )
            if not already_emitted:
                client_tag = McpClient.from_client_name(client_name)
                await emit_event(
                    session,
                    type=EventType.MCP_SESSION_STARTED,
                    severity=EventSeverity.INFO,
                    summary=f"MCP session started for {actor_id}",
                    created_by=actor_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    data={
                        "session_id": session_id,
                        "transport": transport,
                        "client_name": client_name,
                        "client_version": client_version,
                    },
                    tags={client_tag},
                )
        _remember(session_id)
    except (DatabaseIntegrityError, IntegrityError):
        # Lost the cross-worker insert race: another worker/plane committed the
        # row between our lookup and our insert, and the unique index rejected
        # the duplicate (``transaction()`` maps the raw ``IntegrityError`` to
        # ``DatabaseIntegrityError``). The event exists — the desired end state.
        logger.debug("mcp_session_started_already_emitted", actor_id=actor_id)
        _remember(session_id)
    except Exception:
        logger.warning("emit_mcp_session_started_failed", actor_id=actor_id)


def schedule_mcp_session_emit(
    ctx: Context | None,
    *,
    user_agent: str | None,
    session_id: str | None,
    actor_id: str,
    actor_type: str,
) -> None:
    """Fire-and-forget ``mcp.session_started`` from an authenticated request.

    Called by both planes' identity dependencies on every request; the
    non-MCP fast path is two header checks. Never raises and never blocks the
    caller — the DB work runs in a background task (matching the broker's
    auth-failure event pattern). The in-flight check-and-add happens
    synchronously here, before the task is created, so concurrent first
    requests for one session schedule exactly one emit on this process.
    """
    ua = parse_mcp_user_agent(user_agent)
    if ua is None:
        return
    sid = valid_session_id_or_none(session_id)
    if sid is None or sid in _seen_sessions or sid in _in_flight:
        return
    if ctx is None:
        return

    _spawn_emit(
        sid,
        record_mcp_session_started(
            ctx, ua=ua, session_id=sid, actor_id=actor_id, actor_type=actor_type
        ),
    )


def mcp_http_window_key(
    actor_id: str,
    client_name: str | None,
    client_version: str | None,
    *,
    now: float | None = None,
) -> str:
    """The HTTP emit's synthetic session id: one per (agent x client x window).

    Doubles as the ``data.session_id`` the event persists, so (a) the existing
    partial unique index enforces the once-per-window guarantee across
    workers/replicas exactly as it enforces once-per-session-UUID for stdio,
    and (b) the 2-E2 sessions list — which keys rows on ``session_id`` — shows
    one meaningful row per reconnect window on a transport that has no
    protocol session ids at all (spec 2026-07-28 removed them). The clientInfo
    half is a short digest, not the raw strings: ``_meta`` is untrusted input
    and the raw name/version already ride the event ``data`` capped.
    """
    fingerprint = hashlib.sha256(
        f"{client_name or ''}\n{client_version or ''}".encode()
    ).hexdigest()[:12]
    window = int((time.time() if now is None else now) // MCP_HTTP_WINDOW_SECONDS)
    return f"http:{actor_id}:{fingerprint}:{window}"


def schedule_mcp_http_session_emit(
    ctx: Context | None,
    *,
    client_name: str | None,
    client_version: str | None,
    actor_id: str,
    actor_type: str,
) -> None:
    """Fire-and-forget ``mcp.session_started`` from the mounted ``/mcp`` app.

    Called on every authenticated POST the mount serves (phase-3 item 6); the
    dedupe key is the per-window synthetic id, so within one window the fast
    path is one set-membership check. clientInfo comes from the request's
    ``_meta`` (``io.modelcontextprotocol/clientInfo``) and is a SHOULD — an
    absent one still emits, degrading to client-unknown exactly like the
    stdio path (``McpClient.OTHER`` on the wire, ``client_name: null`` in
    ``data``). Values are capped like the UA-derived fields: they persist to
    event ``data`` and are attacker-controlled.
    """
    if ctx is None:
        return
    name = client_name.strip()[:_UA_FIELD_MAX] if client_name and client_name.strip() else None
    version = (
        client_version.strip()[:_UA_FIELD_MAX]
        if client_version and client_version.strip()
        else None
    )
    sid = mcp_http_window_key(actor_id, name, version)
    if sid in _seen_sessions or sid in _in_flight:
        return

    _spawn_emit(
        sid,
        _record_session_started(
            ctx,
            session_id=sid,
            transport=_HTTP_TRANSPORT,
            client_name=name,
            client_version=version,
            actor_id=actor_id,
            actor_type=actor_type,
        ),
    )


def _spawn_emit(sid: str, record: Coroutine[Any, Any, None]) -> None:
    """Run one emit coroutine in the background, tracked under ``sid``.

    The in-flight add is synchronous, before the task is created, so two
    concurrent first requests for one dedupe key can never both schedule.
    Callers must have checked the seen/in-flight sets already (this always
    schedules the coroutine it is given).
    """
    _in_flight.add(sid)
    task = asyncio.create_task(record)
    _background_tasks.add(task)

    def _cleanup(done: asyncio.Task[None]) -> None:
        _background_tasks.discard(done)
        # On failure the key is not in the seen-set, so dropping the
        # in-flight entry lets the next request retry.
        _in_flight.discard(sid)

    task.add_done_callback(_cleanup)
