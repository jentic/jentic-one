"""Shared audit-recording helpers usable across all modules.

The ``AuditEntry`` table lives in the admin database. Admin and auth services
transact against the admin database, so they can record audit entries inside
their own transaction via :func:`record_audit` (atomic). Control and registry
services transact against their own databases, so they must use
:func:`record_audit_best_effort`, which opens a separate admin-database
transaction and never lets an audit failure break the primary mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from jentic_one.admin.repos import AuditRepository
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.redaction import redact_value

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

_meter = get_meter("audit")
audit_events_counter = _meter.create_counter(
    "audit.events",
    description="Audit events by action and target type",
)


async def record_audit(
    session: AsyncSession,
    *,
    action: AuditAction,
    target_type: AuditTargetType,
    target_id: str,
    actor_type: str,
    actor_id: str | None = None,
    actor_session_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    target_parent_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    job_id: str | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    origin: str | None = None,
) -> None:
    """Record an audit entry inside the caller's admin-database transaction.

    Flush-only — participates in the caller's transaction and rolls back with it.
    Use this from services that already transact against the admin database.
    """
    await AuditRepository.record(
        session,
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_session_id=actor_session_id,
        before=before,
        after=after,
        target_parent_id=target_parent_id,
        request_id=request_id,
        trace_id=trace_id,
        job_id=job_id,
        reason=redact_value(reason) if reason is not None else None,
        ip_address=ip_address,
        user_agent=user_agent,
        origin=origin,
    )
    audit_events_counter.add(1, {"action": action, "target_type": target_type})


async def record_audit_best_effort(
    ctx: Context,
    *,
    action: AuditAction,
    target_type: AuditTargetType,
    target_id: str,
    actor_type: str,
    actor_id: str | None = None,
    actor_session_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    target_parent_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    job_id: str | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    origin: str | None = None,
) -> None:
    """Record an audit entry in a *separate* admin-database transaction.

    For services whose primary mutation runs against a non-admin database
    (control, registry). The audit write is best-effort: a failure is logged
    and swallowed so it never rolls back the already-committed mutation.
    """
    try:
        async with ctx.admin_db.transaction() as session:
            await record_audit(
                session,
                action=action,
                target_type=target_type,
                target_id=target_id,
                actor_type=actor_type,
                actor_id=actor_id,
                actor_session_id=actor_session_id,
                before=before,
                after=after,
                target_parent_id=target_parent_id,
                request_id=request_id,
                trace_id=trace_id,
                job_id=job_id,
                reason=reason,
                ip_address=ip_address,
                user_agent=user_agent,
                origin=origin,
            )
    except Exception:
        logger.warning(
            "audit_record_failed",
            action=str(action),
            target_type=str(target_type),
            target_id=target_id,
            exc_info=True,
        )


__all__ = [
    "AuditAction",
    "AuditTargetType",
    "audit_events_counter",
    "record_audit",
    "record_audit_best_effort",
]
