"""Event emission abstraction for cross-module event creation."""

from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.shared.models.events import EVENT_TAGS, EventSeverity, EventTag, EventType
from jentic_one.shared.telemetry.events import resolve_wire_name
from jentic_one.shared.telemetry.sink import get_active_sink

logger = structlog.get_logger(__name__)

_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ZERO_TRACE_ID = "0" * 32

#: Width bound for any variable value an emitter interpolates into an event
#: ``summary`` (``Event.summary`` is ``String(512)``). Error text and registry
#: path templates are unbounded, and an oversized INSERT fails the emit.
MAX_EVENT_SUMMARY_FIELD_LEN = 128


def summary_text(value: str) -> str:
    """Bound one variable value interpolated into an event ``summary``.

    ``Event.summary`` is ``String(512)`` and an oversized INSERT fails the
    emit — which is not always survivable: :func:`emit_credential_access`
    raises by contract (a failed credential-access audit must fail the
    injection, not pass silently), and a failed flush aborts the caller's whole
    Postgres transaction, so even a best-effort emit's swallow cannot contain
    the damage. Every value an emitter draws from a wide or unbounded source —
    a ``String(255)`` display name, an actor id minted by a trusted issuer, a
    registry path template, an API identity tuple — passes through here, so a
    summary's worst case is a sum of known widths rather than of column
    guesses. The ellipsis marks the cut so an operator does not read a clipped
    value as the whole one.
    """
    if len(value) <= MAX_EVENT_SUMMARY_FIELD_LEN:
        return value
    return value[: MAX_EVENT_SUMMARY_FIELD_LEN - 1] + "…"


def summary_label(name: str | None, fallback_id: str) -> str:
    """Name an entity in an event ``summary``: its quoted display name, else its id.

    The UI renders ``summary`` as-is, so a human-readable name beats an opaque
    id. Whitespace runs collapse to one space, control and format characters
    (Unicode ``Cc``/``Cf``, e.g. bidi overrides and zero-width marks) are
    dropped, and a single quote becomes a typographic one so a name cannot close
    the quoting and read as part of the sentence. Name *and* fallback id are
    bounded by :func:`summary_text` — so a label is never wider than
    :data:`MAX_EVENT_SUMMARY_FIELD_LEN` + 2 whichever branch it takes, and a
    summary naming two entities stays inside the column. Callers keep the
    untruncated id in the event's ``data``.
    """
    visible = "".join(
        " " if ch.isspace() else ch
        for ch in (name or "")
        if ch.isspace() or unicodedata.category(ch) not in {"Cc", "Cf"}
    )
    clean = " ".join(visible.replace("'", "\u2019").split())
    if not clean:
        return summary_text(fallback_id)
    return f"'{summary_text(clean)}'"


def valid_trace_id_or_none(trace_id: str | None) -> str | None:
    """Coerce ``trace_id`` to ``None`` unless it is a valid 32-hex trace id.

    ``emit_event`` raises on a malformed ``trace_id`` by contract; emit sites
    that receive a caller-supplied value (raw headers, job payload defaults)
    must sanitise through this helper so a garbage trace id degrades to an
    uncorrelated event instead of failing the surrounding operation (#903).
    The all-zeros id is rejected too: W3C defines it as invalid, and an event
    "correlated" on it would join unrelated requests together.
    """
    if trace_id is not None and _TRACE_ID_PATTERN.match(trace_id) and trace_id != _ZERO_TRACE_ID:
        return trace_id
    return None


def mint_trace_id() -> str:
    """Mint a fresh random 32-hex trace id."""
    return secrets.token_hex(16)


def valid_trace_id_or_minted(trace_id: str | None) -> str:
    """Return ``trace_id`` when it is a valid 32-hex trace id, else mint one.

    For call sites that must always carry a *usable* trace id forward (job
    payload rebuilds, persisted execution rows) rather than degrade to ``None``
    — never the literal ``"unknown"``, which crashed event emission (#903).
    """
    return valid_trace_id_or_none(trace_id) or mint_trace_id()


def _validate_tags(type: str, tags: set[EventTag] | None) -> list[EventTag]:
    """Drop tags whose closed-enum type is not allowed for this event.

    ``EVENT_TAGS`` maps each event to a *tuple* of allowed tag types (an event
    may split along more than one closed enum). Invalid tags are logged and
    discarded; the event still emits (never raises).
    """
    if not tags:
        return []
    allowed = EVENT_TAGS.get(type)
    valid: list[EventTag] = []
    for tag in tags:
        if allowed is not None and isinstance(tag, allowed):
            valid.append(tag)
        else:
            logger.warning("event_tag_dropped", type=type, tag=str(tag))
    return valid


def _forward_to_telemetry(type: str, valid_tags: list[EventTag], actor_type: str | None) -> None:
    """Forward an allowlisted event to the active telemetry sink, if any.

    Best-effort: failures here must never affect the caller. All validated
    closed-enum tags ride along.
    """
    # Consent gate FIRST: telemetry is opt-in. The active sink only exists and
    # reports ``enabled`` when the operator set ``telemetry.enabled: true`` — so
    # if there's no enabled sink, the user has NOT opted in and we send nothing.
    sink = get_active_sink()
    if sink is None or not sink.enabled:
        return
    # Only forward events on the telemetry allowlist (internal-only events stay
    # internal); the tag set has already been validated by the caller. The
    # resolver consults the built-in map first, then the runtime registry.
    wire_name = resolve_wire_name(type)
    if wire_name is None:
        return
    sink.record(wire_name, valid_tags, actor_type)


async def emit_event(
    session: AsyncSession,
    *,
    type: str,
    severity: EventSeverity,
    summary: str,
    created_by: str | None,
    requires_action: bool = False,
    trace_id: str | None = None,
    detail: str | None = None,
    data: dict[str, Any] | None = None,
    execution_id: str | None = None,
    job_id: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
    tags: set[EventTag] | None = None,
) -> str:
    """Create an event within the caller's transaction and return its ID.

    This is the **single entry point** for product telemetry: when telemetry is
    enabled and ``type`` is in ``TELEMETRY_EVENTS``, the event (plus all
    validated closed-enum tags) is also forwarded to the telemetry sink. Services
    never touch the sink directly — they just call ``emit_event``.
    """
    if trace_id is not None and not _TRACE_ID_PATTERN.match(trace_id):
        raise ValueError(f"trace_id must match ^[0-9a-f]{{32}}$, got: {trace_id!r}")

    valid_tags = _validate_tags(type, tags)
    if valid_tags:
        data = {**(data or {}), "tags": [str(t) for t in valid_tags]}

    event = await EventRepository.create(
        session,
        type=type,
        severity=severity,
        summary=summary,
        requires_action=requires_action,
        trace_id=trace_id,
        detail=detail,
        data=data,
        execution_id=execution_id,
        job_id=job_id,
        created_by=created_by,
        actor_id=actor_id,
        actor_type=actor_type,
    )
    # NB: this forwards the event to the sink *before* the caller's transaction
    # commits (``sink.record`` is a synchronous queue put). If the enclosing
    # transaction later rolls back, telemetry will have already emitted — so the
    # anonymous stream may slightly *over-count* relative to persisted state. This
    # is an accepted trade-off: emits sit near transaction end (low rollback risk),
    # telemetry is best-effort/approximate by design, and moving this to an
    # after-commit hook would add coupling for no analytic gain.
    _forward_to_telemetry(type, valid_tags, actor_type)
    return event.id


async def emit_event_best_effort(
    session: AsyncSession,
    *,
    type: str,
    severity: EventSeverity,
    summary: str,
    created_by: str | None,
    tags: set[EventTag] | None = None,
    **kwargs: Any,
) -> None:
    """Call ``emit_event`` but swallow + log any failure.

    For emit points where event/telemetry recording is incidental to the primary
    operation (e.g. after a credential write) and must never surface an error or
    roll back the caller's intent. Telemetry is best-effort by design.
    """
    try:
        await emit_event(
            session,
            type=type,
            severity=severity,
            summary=summary,
            created_by=created_by,
            tags=tags,
            **kwargs,
        )
    except Exception:
        logger.warning("emit_event_best_effort_failed", type=type)


async def emit_credential_access(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_type: str,
    credential_id: str,
    provider: str,
    wire_type: str,
    api_vendor: str,
    api_name: str,
    api_version: str,
    credential_owner: str | None = None,
    credential_name: str | None = None,
    actor_name: str | None = None,
    trace_id: str | None = None,
) -> str:
    """Emit a credential-access audit event and return its ID.

    One record per resolve/decrypt of a stored credential, attributing the use
    to an actor. Called from the single resolve→decrypt→inject seam so each
    credential use produces exactly one event regardless of call-site (sync
    router or async worker). Carries only **non-secret** identifiers — never the
    decrypted material.

    ``actor_id`` is the identity that used the credential; ``created_by`` names
    the credential's owner (``credential_owner``, falling back to the actor when
    the owner is unknown), so under owner-scoped event reads both the owner and
    the actor's owner see the use.

    The summary names BOTH entities in the sentence through
    :func:`summary_label` — the credential by ``credential_name`` (the stored
    ``Credential.name``) and the actor by ``actor_name`` (``Identity.actor_name``,
    carried from whichever resolver authenticated the caller) — each falling
    back to its id. The untruncated ids always ride in ``data``/``actor_id``.

    Unlike most emits this one is **not** best-effort: it raises, and the raise
    fails the whole credential injection. So every interpolated value is width
    bounded (:func:`summary_text` / :func:`summary_label`) — the API identity
    tuple alone is three ``String(100)`` columns, which with two named entities
    would otherwise overflow ``Event.summary`` and take down credential
    injection for a cosmetic reason.
    """
    api = summary_text(
        "/".join(part for part in (api_vendor, api_name, api_version) if part) or api_vendor
    )
    credential = summary_label(credential_name, credential_id)
    actor = summary_label(actor_name, actor_id)
    return await emit_event(
        session,
        type=EventType.CREDENTIAL_ACCESSED,
        severity=EventSeverity.INFO,
        summary=f"Credential {credential} accessed by {actor} for {api}",
        created_by=credential_owner or actor_id,
        trace_id=trace_id,
        actor_id=actor_id,
        actor_type=actor_type,
        data={
            "credential_id": credential_id,
            "provider": provider,
            "wire_type": wire_type,
            "api_vendor": api_vendor,
            "api_name": api_name,
            "api_version": api_version,
        },
    )
