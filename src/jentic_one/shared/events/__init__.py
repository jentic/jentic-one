"""Event emission abstraction for cross-module event creation."""

from __future__ import annotations

import re
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.shared.models.events import EVENT_TAGS, EventSeverity, EventTag, EventType
from jentic_one.shared.telemetry.events import resolve_wire_name
from jentic_one.shared.telemetry.sink import get_active_sink

logger = structlog.get_logger(__name__)

_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _validate_tags(type: str, tags: set[EventTag] | None) -> list[EventTag]:
    """Drop tags whose closed-enum type is not allowed for this event.

    Invalid tags are logged and discarded; the event still emits (never raises).
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
    trace_id: str | None = None,
) -> str:
    """Emit a credential-access audit event (§08 E3.4) and return its ID.

    One record per resolve/decrypt of a stored credential, attributing the use
    to an actor. Called from the single resolve→decrypt→inject seam so each
    credential use produces exactly one event regardless of call-site (sync
    router or async worker). Carries only **non-secret** identifiers — never the
    decrypted material.
    """
    api = "/".join(part for part in (api_vendor, api_name, api_version) if part)
    return await emit_event(
        session,
        type=EventType.CREDENTIAL_ACCESSED,
        severity=EventSeverity.INFO,
        summary=f"Credential {credential_id} accessed by {actor_id} for {api or api_vendor}",
        created_by=actor_id,
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
