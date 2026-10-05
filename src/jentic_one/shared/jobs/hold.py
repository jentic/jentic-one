"""Hold path for require-approval executions.

Provides the atomic create path: a single session call that writes the held
``Job`` row and the ``ExecutionApproval`` row together. The broker execute route
calls :func:`hold_execution` instead of :func:`enqueue_job` when the evaluator
returns a REQUIRE_APPROVAL verdict.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.models.jobs import JobKind, JobStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def compute_execution_fingerprint(
    *,
    agent_id: str,
    credential_id: str,
    method: str,
    path: str,
) -> str:
    """Return a SHA-256 hex digest of the execution's stable identity fields.

    The fingerprint covers ``(agent_id, credential_id, method, path)`` and is
    stored on the ``execution_approvals`` row.  The partial unique index on that
    column (``WHERE state = 'pending'``) prevents a second pending row for the
    same operation — an identical retry joins the existing hold.
    """
    content = f"{agent_id}\x00{credential_id}\x00{method.upper()}\x00{path}"
    return hashlib.sha256(content.encode()).hexdigest()


async def hold_execution(
    session: AsyncSession,
    *,
    execution_id: str,
    agent_id: str,
    credential_id: str,
    matched_rule_id: str | None,
    api_vendor: str,
    api_name: str,
    api_version: str,
    operation_id: str | None,
    method: str,
    path: str,
    trace_id: str | None,
    created_by: str,
    actor_type: str,
    ttl_seconds: int,
    payload: dict[str, Any],
) -> tuple[str, str]:
    """Write a held Job row and its matching ExecutionApproval row.

    Both rows land in the same session flush so they are visible to the caller
    within the same transaction. Returns ``(job_id, approval_id)``.

    The Job starts with ``status=HELD``; the worker skips HELD rows so the
    operation does not run until the approval surface flips the job to QUEUED.
    The ExecutionApproval row starts in ``state='pending'`` with a TTL-derived
    ``expires_at``; the expiry sweep fails the held job when the window lapses.
    """
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    fp = compute_execution_fingerprint(
        agent_id=agent_id,
        credential_id=credential_id,
        method=method,
        path=path,
    )

    job: Any = Job(
        kind=str(JobKind.EXECUTION),
        status=JobStatus.HELD,
        execution_id=execution_id,
        payload=dict(payload),
        created_by=created_by,
        actor_type=actor_type,
    )
    session.add(job)
    await session.flush()
    job_id = str(job.id)

    approval: Any = ExecutionApproval(
        job_id=job_id,
        agent_id=agent_id,
        credential_id=credential_id,
        api_vendor=api_vendor,
        api_name=api_name,
        api_version=api_version,
        operation_id=operation_id,
        method=method.upper(),
        path=path,
        request_fingerprint=fp,
        matched_rule_id=matched_rule_id,
        state="pending",
        expires_at=expires_at,
        trace_id=trace_id,
        execution_id=execution_id,
        created_by=agent_id,
    )
    session.add(approval)
    await session.flush()
    approval_id = str(approval.id)

    return job_id, approval_id


async def count_pending_by_agent(session: AsyncSession, agent_id: str) -> int:
    """Return the number of pending approval rows for the given agent."""
    stmt = (
        select(func.count())
        .select_from(ExecutionApproval)
        .where(
            ExecutionApproval.agent_id == agent_id,
            ExecutionApproval.state == "pending",
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def get_pending_by_fingerprint(
    session: AsyncSession, fingerprint: str
) -> ExecutionApproval | None:
    """Return a pending approval row with the given request fingerprint, or None."""
    stmt = select(ExecutionApproval).where(
        ExecutionApproval.request_fingerprint == fingerprint,
        ExecutionApproval.state == "pending",
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_approved_by_job_id(session: AsyncSession, job_id: str) -> ExecutionApproval | None:
    """Return the approved execution_approvals row for a job, or None."""
    stmt = select(ExecutionApproval).where(
        ExecutionApproval.job_id == job_id,
        ExecutionApproval.state == "approved",
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
