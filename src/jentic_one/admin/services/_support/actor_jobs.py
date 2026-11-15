"""Cancel an actor's queued execution jobs when the actor loses its authority.

Shared by ``UserService`` (disable, delete) and ``AgentService`` (disable,
archive). An execution job runs with the authority of the actor that enqueued
it, so a job still queued when that actor is disabled, archived or deleted can
never run legitimately. Cancelling it up front keeps the queue honest; the
worker's execution-time actor check stays the backstop for anything this sweep
cannot reach (a job already ``running``, or one enqueued concurrently).

The ``jobs`` table lives in the admin database, like ``users`` and ``agents``,
so the sweep runs in the caller's transaction: it commits with the status
change, or rolls back with it. Cancellation is final; re-enabling the actor
does not requeue anything.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text

from jentic_one.admin.repos import JobRepository
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity

logger = structlog.get_logger(__name__)


async def cancel_queued_executions_for_actor(
    session: Any,
    *,
    actor_id: str,
    target_type: AuditTargetType,
    cause: str,
    identity: Identity,
) -> int:
    """Cancel ``actor_id``'s queued execution jobs inside the caller's transaction.

    ``target_type`` names the kind of actor being swept (``user`` or
    ``agent``) and ``cause`` what happened to it, phrased to follow the actor
    kind (``"was disabled"``, ``"was archived"``, ...). One ``UPDATE`` audit
    entry is recorded on the actor, attributed to the caller, when at least
    one job is cancelled; nothing is recorded when the queue holds none.
    Returns the number of jobs cancelled.
    """
    job_ids = await JobRepository.cancel_queued_executions_for_actor(session, actor_id)
    if not job_ids:
        return 0
    await record_audit(
        session,
        action=AuditAction.UPDATE,
        target_type=target_type,
        target_id=actor_id,
        actor_type=identity.actor_type,
        actor_id=identity.sub,
        after={"cancelled_queued_execution_jobs": len(job_ids), "job_ids": job_ids},
        reason=f"queued execution jobs cancelled: {target_type.value} {cause}",
        origin=identity.origin.value,
    )
    logger.info(
        "queued_execution_jobs_cancelled",
        target_type=target_type.value,
        target_id=actor_id,
        actor_id=identity.sub,
        cause=cause,
        cancelled_jobs=len(job_ids),
    )
    return len(job_ids)


async def deactivate_user_control_resources(session: Any, *, user_id: str) -> list[str]:
    """Deactivate ``user_id``'s control-DB credentials, drop tokens, and cancel open sessions."""
    params = {"uid": user_id}
    rows = await session.execute(
        text("SELECT id FROM credentials WHERE created_by = CAST(:uid AS VARCHAR) ORDER BY id"),
        params,
    )
    credential_ids = [str(row[0]) for row in rows.all()]
    await session.execute(
        text(
            "DELETE FROM oauth_tokens WHERE credential_id IN "
            "(SELECT id FROM credentials WHERE created_by = CAST(:uid AS VARCHAR))"
        ),
        params,
    )
    await session.execute(
        text("UPDATE credentials SET active = FALSE WHERE created_by = CAST(:uid AS VARCHAR)"),
        params,
    )
    await session.execute(
        text(
            "UPDATE connect_sessions SET state = 'failed', error_code = 'cancelled' "
            "WHERE initiator_actor_id = CAST(:uid AS VARCHAR) "
            "AND state IN ('created', 'awaiting_app', 'polling')"
        ),
        params,
    )
    session.expire_all()
    return credential_ids
