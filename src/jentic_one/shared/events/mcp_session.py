"""``mcp.session_started`` emission — first authenticated request per MCP session.

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
import re
from dataclasses import dataclass

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
    try:
        async with ctx.admin_db.transaction() as session:
            already_emitted = await EventRepository.exists_with_data_value(
                session,
                event_type=EventType.MCP_SESSION_STARTED,
                key="session_id",
                value=session_id,
            )
            if not already_emitted:
                client_tag = McpClient.from_client_name(ua.client_name)
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
                        "transport": _STDIO_TRANSPORT,
                        "client_name": ua.client_name,
                        "client_version": ua.client_version,
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

    _in_flight.add(sid)
    task = asyncio.create_task(
        record_mcp_session_started(
            ctx, ua=ua, session_id=sid, actor_id=actor_id, actor_type=actor_type
        )
    )
    _background_tasks.add(task)

    def _cleanup(done: asyncio.Task[None]) -> None:
        _background_tasks.discard(done)
        # On failure the session is not in the seen-set, so dropping the
        # in-flight entry lets the next request retry.
        _in_flight.discard(sid)

    task.add_done_callback(_cleanup)
