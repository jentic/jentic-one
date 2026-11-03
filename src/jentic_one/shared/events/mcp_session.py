"""``mcp.session_started`` emission — first authenticated request per MCP session.

Two transports emit this event, sharing one dedupe machine (the shared-state
claim, the events-table lookup, and the partial unique index on
``(type, data->>'session_id')``) but with different **keys**:

- **stdio** (lane D, issue #1177): once per session UUID relayed by the Go
  server — :func:`schedule_mcp_session_emit`, described below.
- **HTTP**: the daemon-native ``/mcp`` mount sees MCP
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
three-layered: a claim on the process's shared-state backend
(``Context.state_backend``, key ``mcp-session:<session id>``) taken before
any task is scheduled, so concurrent first requests on every process sharing
the backend schedule at most one emit and a session already emitted costs one
backend lookup (a backend that refuses the claim falls through to the next
two layers rather than dropping the event); one indexed events-table lookup
on ``data.session_id`` (the store of record, shared by every process
whatever the backend); and — because the lookup is check-then-insert — a
partial unique index on
``(type, data->>'session_id')`` scoped to this event type
(``uq_events_mcp_session_started_session``). The loser of a cross-process
race gets an ``IntegrityError`` and skips, so the store holds exactly one row
per session id. Emission is fire-and-forget in a background task so the auth
path never waits on the database, and it rides ``emit_event``'s existing
consent gate — the telemetry wire carries at most the closed ``McpClient``
tag, while the internal event's ``data`` holds the full clientInfo, the
transport, and the session id for the UI (two-plane pattern).
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

#: Transport for the header-seam path. The mounted HTTP app emits with
#: its own transport value and windowed dedupe key — not through this module's
#: once-per-session-id path.
_STDIO_TRANSPORT = "stdio"

#: Transport for the mounted ``/mcp`` app's windowed emit path.
_HTTP_TRANSPORT = "http"

#: The HTTP emit window: at most one ``mcp.session_started``
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

#: Per-agent cap on *distinct* clientInfo dedupe keys admitted per window
#:. Without it an authenticated agent varying ``_meta`` clientInfo
#: per request mints one events-table row per request for the whole window —
#: the unique index dedupes identical keys only, never distinct ones. Sixteen
#: distinct (name, version) pairs per 6-hour bucket is far beyond any legit
#: multi-client agent (each real client is one pair per version); past the cap
#: the emit is skipped and a warning logged once per (agent, window).
MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW = 16

#: Longest UA-derived field persisted to event ``data``. The User-Agent is
#: untrusted input; without a cap a hostile client could grow every
#: ``mcp.session_started`` row by kilobytes of junk.
_UA_FIELD_MAX = 128

#: Shared-state namespace of the per-session claim (``mcp-session:<id>``). A
#: claim taken before an emit is scheduled lives :data:`_CLAIM_TTL_S`; once the
#: event is recorded (here or, per the table lookup, by another process) it is
#: rewritten to live :data:`_SEEN_TTL_S`, so later requests short-circuit on it.
SESSION_NAMESPACE = "mcp-session"
#: Shared-state namespace of the per-(agent, window) distinct-key counter behind
#: :data:`MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW` (``mcp-session-cap:<agent>:<window>``).
CLIENT_CAP_NAMESPACE = "mcp-session-cap"

#: How long an unconfirmed claim holds a session id. An emit that fails leaves
#: its claim to expire, so the next request after this retries it.
_CLAIM_TTL_S = 60.0
#: How long a recorded session id short-circuits. Its expiry only costs one
#: table lookup for a session still sending requests, never a duplicate event.
_SEEN_TTL_S = float(MCP_HTTP_WINDOW_SECONDS)

#: Cap on concurrently in-flight emits on this process: each holds a
#: background task with an open admin-DB transaction, so the transient
#: population must not be attacker-paced. At the cap new emits are dropped
#: before their claim is taken, so a later request retries once the backlog
#: drains.
_IN_FLIGHT_MAX = 1024

#: Strong references to this process's running emit tasks (the event loop
#: holds only weak ones). Process-local by nature: a task runs where it was
#: created. Dedupe state lives on the shared-state backend, never here.
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


def _session_key(session_id: str) -> str:
    return f"{SESSION_NAMESPACE}:{session_id}"


async def _mark_recorded(ctx: Context, session_id: str) -> None:
    """Turn the session's claim into a long-lived "recorded" marker (best-effort)."""
    try:
        await ctx.state_backend.set(_session_key(session_id), b"recorded", ttl_s=_SEEN_TTL_S)
    except Exception as exc:
        # The event is recorded; a lost marker costs one table lookup later.
        logger.debug("mcp_session_marker_not_stored", error=type(exc).__name__)


async def _claim(ctx: Context, session_id: str) -> bool:
    """Claim ``session_id`` for one emit, across every process sharing the backend.

    ``False`` when the id is already claimed or recorded: the emit is
    skipped. A backend that refuses (its ``mcp-session`` namespace is full)
    or fails the write answers ``True``: the claim only saves the table
    lookup, and the lookup plus the unique index still hold the
    once-per-session guarantee. Failing closed instead would let one caller
    minting session ids fill the namespace and silence every other
    session's event until the markers expire. The in-flight cap still bounds
    the emits a full namespace lets through.
    """
    try:
        return await ctx.state_backend.set_if_absent(
            _session_key(session_id), b"claimed", ttl_s=_CLAIM_TTL_S
        )
    except Exception as exc:
        logger.debug("mcp_session_claim_unavailable", error=type(exc).__name__)
        return True


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
    treated as "already emitted". Other failures are logged and the claim is
    left to expire, so a request for the session after that retries.
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
    else (table lookup, unique-index race tolerance, recorded-marker
    priming) is identical.
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
    except (DatabaseIntegrityError, IntegrityError):
        # Lost the cross-worker insert race: another worker/plane committed the
        # row between our lookup and our insert, and the unique index rejected
        # the duplicate (``transaction()`` maps the raw ``IntegrityError`` to
        # ``DatabaseIntegrityError``). The event exists — the desired end state.
        logger.debug("mcp_session_started_already_emitted", actor_id=actor_id)
    except Exception:
        logger.warning("emit_mcp_session_started_failed", actor_id=actor_id)
        return
    await _mark_recorded(ctx, session_id)


async def schedule_mcp_session_emit(
    ctx: Context | None,
    *,
    user_agent: str | None,
    session_id: str | None,
    actor_id: str,
    actor_type: str,
) -> None:
    """Fire-and-forget ``mcp.session_started`` from an authenticated request.

    Called by both planes' identity dependencies on every request; the
    non-MCP fast path is two header checks, with no await. Never raises and
    never waits on the database — the DB work runs in a background task
    (matching the broker's auth-failure event pattern). The session's claim
    on the shared-state backend is taken here, before the task is created,
    so concurrent first requests for one session schedule exactly one emit
    on every process sharing the backend.
    """
    ua = parse_mcp_user_agent(user_agent)
    if ua is None:
        return
    sid = valid_session_id_or_none(session_id)
    if sid is None or ctx is None:
        return
    if not _in_flight_has_room() or not await _claim(ctx, sid):
        return

    _spawn_emit(
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
    and the raw name/version already ride the event ``data`` capped. The
    digest inputs are case-folded (one row per client regardless of case —
    matching the ``McpClient`` tag's case-insensitive mapping) and
    length-prefixed before joining, so ``("x\\ny", "z")`` and ``("x", "y\\nz")``
    can never collide on the join delimiter.
    """
    fingerprint = hashlib.sha256(
        f"{_digest_component(client_name)}\n{_digest_component(client_version)}".encode()
    ).hexdigest()[:12]
    window = int((time.time() if now is None else now) // MCP_HTTP_WINDOW_SECONDS)
    return f"http:{actor_id}:{fingerprint}:{window}"


def _digest_component(value: str | None) -> str:
    """One unambiguous digest-input token: case-folded and length-prefixed."""
    text = (value or "").casefold()
    return f"{len(text)}:{text}"


async def schedule_mcp_http_session_emit(
    ctx: Context | None,
    *,
    client_name: str | None,
    client_version: str | None,
    actor_id: str,
    actor_type: str,
) -> None:
    """Fire-and-forget ``mcp.session_started`` from the mounted ``/mcp`` app.

    Called on every authenticated POST the mount serves; the
    dedupe key is the per-window synthetic id, so within one window the
    repeat cost is one refused claim on the shared-state backend. clientInfo
    comes from the request's ``_meta`` (``io.modelcontextprotocol/clientInfo``)
    and is a SHOULD — an absent one still emits, degrading to client-unknown
    exactly like the stdio path (``McpClient.OTHER`` on the wire,
    ``client_name: null`` in ``data``). Values are capped like the UA-derived
    fields: they persist to event ``data`` and are attacker-controlled.
    Distinct keys are capped per (agent, window) —
    :data:`MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW`, counted on the shared
    backend — so an agent varying clientInfo per request cannot mint a row
    per request, however many processes it spreads them over.
    """
    if ctx is None:
        return
    name = client_name.strip()[:_UA_FIELD_MAX] if client_name and client_name.strip() else None
    version = (
        client_version.strip()[:_UA_FIELD_MAX]
        if client_version and client_version.strip()
        else None
    )
    now = time.time()
    sid = mcp_http_window_key(actor_id, name, version, now=now)
    if not _in_flight_has_room() or not await _claim(ctx, sid):
        return
    window = int(now // MCP_HTTP_WINDOW_SECONDS)
    if not await _admit_agent_window_key(ctx, actor_id, window):
        return

    _spawn_emit(
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


def _in_flight_has_room() -> bool:
    """Drop new emits while this process's in-flight backlog is at the cap.

    Checked before the claim is taken, so a dropped key stays unclaimed and a
    later request retries it once the backlog drains — bounded memory at the
    cost of a delayed row under a flood, never a lost one.
    """
    if len(_background_tasks) < _IN_FLIGHT_MAX:
        return True
    logger.debug("mcp_session_emit_dropped_in_flight_cap", in_flight=len(_background_tasks))
    return False


async def _admit_agent_window_key(ctx: Context, actor_id: str, window: int) -> bool:
    """Admit one more *distinct* HTTP dedupe key for this (agent, window).

    Callers have just claimed the key, so it is new. The count is an atomic
    increment on the shared-state backend, so the cap holds across
    processes. Past :data:`MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW` the emit
    is skipped and a warning logged once per (agent, window) — one flooding
    agent stops minting rows while every other agent's counter is untouched.
    A key retried after its claim expired (or while the ``mcp-session``
    namespace refuses claims) re-counts, tightening the cap for that agent —
    acceptable for a defensive bound. A backend that
    refuses or fails the increment skips the emit.
    """
    try:
        count = await ctx.state_backend.incr_with_ttl(
            f"{CLIENT_CAP_NAMESPACE}:{actor_id}:{window}", ttl_s=float(MCP_HTTP_WINDOW_SECONDS)
        )
    except Exception as exc:
        logger.debug("mcp_http_session_client_cap_skipped", error=type(exc).__name__)
        return False
    if count <= MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW:
        return True
    if count == MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW + 1:
        logger.warning(
            "mcp_http_session_client_cap_reached",
            actor_id=actor_id,
            window=window,
            cap=MCP_HTTP_MAX_CLIENTS_PER_AGENT_WINDOW,
        )
    return False


def _spawn_emit(record: Coroutine[Any, Any, None]) -> None:
    """Run one emit coroutine in the background, holding a reference until it ends."""
    task = asyncio.create_task(record)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
