"""Hold service — list, read, decide on and withdraw held executions, and read their result.

A hold is an execution a require-approval rule parked for a human, stored as an
approval row over a held execution job. The agent that filed a pending hold
may explain it once; every read of the hold carries that explanation.
Visibility follows ``build_access_filters``: ``org:admin`` sees every hold, a
user sees holds filed by agents they own, an agent sees its own. Deciding
needs that same reviewer visibility — the agent's owner or an ``org:admin``;
an ownerless agent's holds are admin-only — and is refused outright for any
agent caller, whatever its scopes. Withdrawing and explaining are open only to
the identity that filed the hold: a caller who can see the hold but did not file
it is refused (403); one who cannot see it is told it does not exist (404).

The holds routes take no ``jobs:*`` permission, so the service reads a hold's
job only through ``JobRepository.get_held_execution`` and
``held_execution_statuses``: the held execution job its approval names, under
the caller's job visibility too. No hold reaches any other job.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import structlog

from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import (
    AgentRepository,
    AuditRepository,
    JobResultRepository,
)
from jentic_one.admin.repos.agent_credential_binding_repo import AgentCredentialBindingRepository
from jentic_one.admin.repos.held_execution_repo import HeldExecutionRepository
from jentic_one.admin.repos.job_repo import JobRepository
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.admin.services.errors import (
    HeldExecutionAlreadyDecidedError,
    HeldExecutionAlreadyExplainedError,
    HeldExecutionExplainForbiddenError,
    HeldExecutionForbiddenError,
    HeldExecutionJobNotHeldError,
    HeldExecutionNotFoundError,
    HeldExecutionRenewalForbiddenError,
    HeldExecutionRenewalInvalidError,
    HeldExecutionRenewalUnavailableError,
    HeldExecutionResultExpiredError,
    HeldExecutionResultNotReadyError,
    HeldExecutionWithdrawForbiddenError,
    InvalidInputError,
)
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.schemas.held_executions import (
    DecideInput,
    HeldExecutionDetailView,
    HeldExecutionView,
    HeldRequestView,
    RenewalContextView,
)
from jentic_one.admin.services.schemas.jobs import JobResultView
from jentic_one.shared.agent_text import clean_agent_text
from jentic_one.shared.auth.grant_policy import GrantPolicy, grant_end_reason
from jentic_one.shared.auth.grant_renewal import (
    GrantRenewalPolicy,
    GrantRenewalRefusedError,
    GrantRenewerProtocol,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.hold import (
    ENCRYPTED_PAYLOAD_KEY,
    PROBLEM_CONTENT_TYPE,
    approval_denied_problem,
    held_call_summary,
    renewed_without_run_problem,
)
from jentic_one.shared.jobs.wakeup import notify_on_commit
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.held_executions import (
    ApprovalDecision,
    HeldExecutionKind,
    HeldExecutionState,
)
from jentic_one.shared.models.jobs import JobKind, JobStatus
from jentic_one.shared.pagination import Page, decode_cursor, encode_cursor

logger = structlog.get_logger(__name__)

#: Cap on the held body shown to a reviewer; the page notes the truncation.
_MAX_REVIEW_BODY_BYTES = 64 << 10

#: A hold's call has a result once its job is completed, or failed with a
#: recorded problem (a denied or expired approval, or a run-time refusal).
_RESULT_STATUSES = frozenset({JobStatus.COMPLETED.value, JobStatus.FAILED.value})


def _execution_status(job_status: str | None) -> str | None:
    """A held execution job's status as the holds surface names it.

    ``dead_letter`` is a worker retry detail: to a hold it is a failed call.
    """
    if job_status == JobStatus.DEAD_LETTER.value:
        return JobStatus.FAILED.value
    return job_status


@dataclass(frozen=True, slots=True)
class _RenewalPlan:
    """A renewal hold's approval: its binding, and the policy to re-grant (``None``:
    the binding was renewed through its own endpoint and is live again)."""

    agent_id: str
    credential_id: str
    policy: GrantRenewalPolicy | None = None


def _renewal_forbidden() -> HeldExecutionRenewalForbiddenError:
    return HeldExecutionRenewalForbiddenError(
        "Only someone who may renew this access can grant it: a person who owns the "
        "agent and the credential, or an org admin. You can deny the call."
    )


class HeldExecutionService:
    """Read and decide execution approvals."""

    def __init__(self, ctx: Context, *, grant_renewer: GrantRenewerProtocol | None = None) -> None:
        self._ctx = ctx
        # Renews a ``grant_renewal`` hold's binding (installed by the
        # composition root; absent, such a hold can only be denied).
        self._grant_renewer = grant_renewer

    async def list_all(
        self,
        *,
        identity: Identity,
        state: HeldExecutionState | None = None,
        agent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[HeldExecutionView]:
        cursor_key = decode_cursor(cursor, id_kind="str") if cursor is not None else None
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.session() as session:
            rows = await HeldExecutionRepository.list_all(
                session,
                state=state.value if state is not None else None,
                agent_id=agent_id,
                limit=limit + 1,
                cursor=cursor_key,
                filters=filters,
            )
            statuses = await JobRepository.held_execution_statuses(
                session,
                [r.job_id for r in rows[:limit]],
                filters=build_access_filters(identity, Job),
            )
        has_more = len(rows) > limit
        data = [
            HeldExecutionView.model_validate(r).model_copy(
                update={"execution_status": _execution_status(statuses.get(r.job_id))}
            )
            for r in rows[:limit]
        ]
        next_cursor = encode_cursor(data[-1].created_at, data[-1].id) if has_more else None
        return Page(data=data, has_more=has_more, next_cursor=next_cursor)

    async def get(self, approval_id: str, *, identity: Identity) -> HeldExecutionDetailView:
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.session() as session:
            row = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
            if row is None:
                raise HeldExecutionNotFoundError(approval_id)
            agent = await AgentRepository.get_by_id(session, row.agent_id)
            job = await JobRepository.get_held_execution(
                session, row.job_id, filters=build_access_filters(identity, Job)
            )
            binding = (
                await AgentCredentialBindingRepository.get(
                    session, agent_id=row.agent_id, credential_id=row.credential_id
                )
                if row.kind == HeldExecutionKind.GRANT_RENEWAL.value
                else None
            )
        view = HeldExecutionView.model_validate(row)
        return HeldExecutionDetailView(
            **view.model_dump(exclude={"execution_status"}),
            execution_status=_execution_status(str(job.status) if job is not None else None),
            agent_name=agent.name if agent is not None else None,
            agent_owner_id=agent.owner_id if agent is not None else None,
            request=self._held_request(job.payload if job is not None else None),
            renewal=(
                await self._renewal_context(row, binding, identity=identity)
                if row.kind == HeldExecutionKind.GRANT_RENEWAL.value
                else None
            ),
        )

    async def _renewal_context(
        self, row: HeldExecution, binding: Any, *, identity: Identity
    ) -> RenewalContextView:
        """The binding a renewal hold would renew, and whether ``identity`` may renew it."""
        can_renew = self._grant_renewer is not None and await self._grant_renewer.may_renew(
            identity=identity, agent_id=row.agent_id, credential_id=row.credential_id
        )
        renew_on_decide = self._grant_renewer is not None
        if binding is None:
            return RenewalContextView(can_renew=False, renew_on_decide=renew_on_decide)
        reason = grant_end_reason(GrantPolicy.from_row(binding))
        return RenewalContextView(
            expired_reason=reason.value if reason is not None else None,
            last_used_at=binding.last_used_at,
            expires_at=binding.expires_at,
            max_idle_period=binding.max_idle_period,
            idle_warning=binding.idle_warning,
            after_expiry=binding.after_expiry,
            max_quota=binding.max_quota,
            quota_remaining=binding.quota_remaining,
            can_renew=can_renew,
            renew_on_decide=renew_on_decide,
        )

    async def get_result(self, approval_id: str, *, identity: Identity) -> JobResultView:
        """The finished call's result: the upstream response, or why it never ran.

        Read through the hold, never by job id: the job is the held execution
        the visible approval names (``JobRepository.get_held_execution``), and
        its result row is read only after that lookup succeeds.
        """
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.session() as session:
            row = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
            if row is None:
                raise HeldExecutionNotFoundError(approval_id)
            job = await JobRepository.get_held_execution(
                session, row.job_id, filters=build_access_filters(identity, Job)
            )
            if job is None:
                raise HeldExecutionNotFoundError(approval_id)
            status = _execution_status(str(job.status)) or ""
            if status not in _RESULT_STATUSES:
                raise HeldExecutionResultNotReadyError(approval_id, status)
            result = await JobResultRepository.get_by_job_id(session, job.id)
            if result is None:
                if status == JobStatus.FAILED.value:
                    # A failure with no recorded problem body has nothing to show.
                    raise HeldExecutionResultNotReadyError(approval_id, status)
                raise HeldExecutionResultExpiredError(approval_id)
            if (
                result.available_until is not None
                and result.available_until < await HeldExecutionRepository.now(session)
            ):
                raise HeldExecutionResultExpiredError(approval_id)
        return JobResultService.to_view(result)

    def _held_request(self, payload: dict[str, Any] | None) -> HeldRequestView | None:
        """Decrypt the held job payload into what the reviewer is approving.

        Upstream credentials are injected only when the job runs, so the
        payload never carries them.
        """
        if not payload:
            return None
        if ENCRYPTED_PAYLOAD_KEY in payload:
            if not self._ctx.has_encryption_keyset:
                logger.warning("held_payload_unreadable_without_keyset")
                return None
            payload = json.loads(self._ctx.encryption.decrypt(str(payload[ENCRYPTED_PAYLOAD_KEY])))
        raw = base64.b64decode(payload["body_b64"]) if payload.get("body_b64") else b""
        truncated = len(raw) > _MAX_REVIEW_BODY_BYTES
        body = raw[:_MAX_REVIEW_BODY_BYTES].decode("utf-8", errors="replace") if raw else None
        return HeldRequestView(
            method=str(payload.get("method", "")),
            url=str(payload.get("upstream_url", "")),
            body=body,
            body_truncated=truncated,
        )

    async def decide(
        self, approval_id: str, body: DecideInput, *, identity: Identity
    ) -> HeldExecutionView:
        """Approve or deny a pending approval — compare-and-set, first reviewer wins.

        In one transaction: approve moves the held job to ``queued``; deny fails
        it with a permission-denied problem as its result. A job that is no
        longer ``held`` raises ``HeldExecutionJobNotHeldError`` and rolls
        the decision back, so the approval stays ``pending``. The decision is
        audit-logged and emits ``execution.approval_decided``.
        """
        if identity.actor_type == ActorType.AGENT:
            raise HeldExecutionForbiddenError(
                f"Agent '{identity.sub}' cannot decide execution approvals"
            )
        approve = body.decision == ApprovalDecision.APPROVE
        new_state = HeldExecutionState.APPROVED if approve else HeldExecutionState.DENIED
        filters = build_access_filters(identity, HeldExecution, for_write=True)
        plan = await self._plan_renewal(approval_id, body, filters, identity=identity)
        renewed = plan is not None
        # A renewal's reviewer may renew without running a stale call.
        release = approve and (not renewed or body.run)
        async with self._ctx.admin_db.transaction() as session:
            now = await HeldExecutionRepository.now(session)
            visible = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
            if visible is None:
                raise HeldExecutionNotFoundError(approval_id)
            updated = await HeldExecutionRepository.decide(
                session,
                approval_id,
                new_state=new_state.value,
                decided_by=identity.sub,
                decision_reason=body.reason,
                now=now,
            )
            if updated is None:
                # Read the state from the database: the row loaded above is
                # cached in this session and predates a concurrent settle.
                state = (
                    await HeldExecutionRepository.get_state(session, approval_id) or visible.state
                )
                if state == HeldExecutionState.PENDING:
                    state = HeldExecutionState.EXPIRED.value
                raise HeldExecutionAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state}"
                )
            # The decision holds the hold now: re-grant the binding in the same
            # transaction, so a failure here or below renews nothing.
            if plan is not None:
                await self._write_renewal(session, plan, identity=identity)
            moved = await JobRepository.transition(
                session,
                updated.job_id,
                from_status=JobStatus.HELD,
                to_status=JobStatus.QUEUED if release else JobStatus.FAILED,
            )
            if not moved:
                # Raising rolls the decision back with the transaction.
                raise HeldExecutionJobNotHeldError(approval_id, updated.job_id)
            if release:
                # The released job is claimable once this commits; wake any
                # worker in this process instead of waiting for its next poll.
                notify_on_commit(session)
            else:
                await JobRepository.update(
                    session,
                    updated.job_id,
                    error="access renewed, call not run" if approve else "approval denied",
                )
                await JobResultRepository.create(
                    session,
                    job_id=updated.job_id,
                    kind=JobKind.EXECUTION.value,
                    body=(
                        renewed_without_run_problem(approval_id)
                        if approve
                        else approval_denied_problem(approval_id, body.reason)
                    ),
                    content_type=PROBLEM_CONTENT_TYPE,
                    created_by=identity.sub,
                )
            agent = await AgentRepository.get_by_id(session, updated.agent_id)
            await AuditRepository.record(
                session,
                action=AuditAction.APPROVE if approve else AuditAction.DENY,
                target_type=AuditTargetType.HELD_EXECUTION,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"state": HeldExecutionState.PENDING.value},
                after={
                    "state": new_state.value,
                    **({"renewed": True, "ran": release} if renewed else {}),
                },
                reason=body.reason,
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_DECIDED,
                severity=EventSeverity.INFO,
                summary=held_call_summary(
                    agent_name=agent.name if agent is not None else None,
                    agent_id=updated.agent_id,
                    method=updated.method,
                    path=updated.path,
                    outcome=new_state.value,
                ),
                job_id=updated.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "held_execution_id": approval_id,
                    "agent_id": updated.agent_id,
                    "decision": body.decision.value,
                    "method": updated.method,
                    "path": updated.path,
                    "kind": updated.kind,
                    **({"renewed": True, "ran": release} if renewed else {}),
                },
            )
            view = HeldExecutionView.model_validate(updated).model_copy(
                update={
                    "execution_status": (
                        JobStatus.QUEUED.value if release else JobStatus.FAILED.value
                    ),
                }
            )
        if plan is not None and plan.policy is not None:
            self._ctx.invalidate_broker_caches(
                agent_id=plan.agent_id, credential_id=plan.credential_id
            )
        logger.info(
            "held_execution_decided",
            approval_id=approval_id,
            decision=body.decision.value,
            actor_id=identity.sub,
        )
        return view

    async def _binding_live(self, agent_id: str, credential_id: str) -> bool:
        """Whether the binding exists, is not suspended, and no limit has ended it."""
        async with self._ctx.admin_db.session() as session:
            binding = await AgentCredentialBindingRepository.get(
                session, agent_id=agent_id, credential_id=credential_id
            )
        return (
            binding is not None
            and not binding.suspended
            and grant_end_reason(GrantPolicy.from_row(binding)) is None
        )

    async def _plan_renewal(
        self,
        approval_id: str,
        body: DecideInput,
        filters: list[Any],
        *,
        identity: Identity,
    ) -> _RenewalPlan | None:
        """What approving a ``grant_renewal`` hold re-grants, checked before deciding.

        ``None`` for any other decision. Only approving a renewal hold renews,
        and only with the fresh policy and a reviewer who may renew the binding
        directly (the renew endpoint's own checks, through the installed
        renewer). Nothing is written here: the renewal itself is written in
        the decision's transaction, after the decision has claimed the pending
        hold (:meth:`decide`), so a hold that has expired or that another
        reviewer settled first renews nothing.

        Approving without ``renewal`` is accepted once the binding is live
        again, renewed through its own endpoint (the only way where this
        process has no renewer): it releases the call, or with ``run: false``
        fails it, and reports the hold as renewed.
        """
        async with self._ctx.admin_db.session() as session:
            row = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
        if row is None:
            raise HeldExecutionNotFoundError(approval_id)
        is_renewal = row.kind == HeldExecutionKind.GRANT_RENEWAL.value
        if not is_renewal:
            if body.renewal is not None:
                raise HeldExecutionRenewalInvalidError(
                    "Only a held call whose access ended (kind grant_renewal) takes `renewal`"
                )
            return None
        if body.decision != ApprovalDecision.APPROVE:
            return None
        if row.state != HeldExecutionState.PENDING.value:
            raise HeldExecutionAlreadyDecidedError(
                f"Execution approval '{approval_id}' is already {row.state}"
            )
        if body.renewal is None:
            # Renewed through the binding's own endpoint (where this process
            # cannot renew, say): approving only releases the call, which the
            # worker re-authorizes when it runs.
            if await self._binding_live(row.agent_id, row.credential_id):
                return _RenewalPlan(agent_id=row.agent_id, credential_id=row.credential_id)
            raise HeldExecutionRenewalInvalidError(
                "Approving a renewal needs the binding's fresh policy (`renewal`), "
                "or the binding renewed first"
            )
        if self._grant_renewer is None:
            raise HeldExecutionRenewalUnavailableError(
                "This deployment cannot renew agent access from a held call; renew it from "
                "the agent's API, then deny this call"
            )
        if not await self._grant_renewer.may_renew(
            identity=identity, agent_id=row.agent_id, credential_id=row.credential_id
        ):
            raise _renewal_forbidden()
        return _RenewalPlan(
            agent_id=row.agent_id, credential_id=row.credential_id, policy=body.renewal
        )

    async def _write_renewal(self, session: Any, plan: _RenewalPlan, *, identity: Identity) -> None:
        """Re-grant the binding in the decision's transaction (a plan with a policy only)."""
        if plan.policy is None or self._grant_renewer is None:
            return
        try:
            await self._grant_renewer.renew(
                identity=identity,
                agent_id=plan.agent_id,
                credential_id=plan.credential_id,
                policy=plan.policy,
                session=session,
            )
        except GrantRenewalRefusedError as exc:
            raise _renewal_forbidden() from exc

    @staticmethod
    async def _require_filed(
        session: Any,
        approval_id: str,
        identity: Identity,
        *,
        forbidden: type[HeldExecutionExplainForbiddenError | HeldExecutionWithdrawForbiddenError],
        for_update: bool = False,
    ) -> HeldExecution:
        """The hold the caller filed, read under the caller's visibility.

        Missing or invisible is ``HeldExecutionNotFoundError``, so the hold's
        existence never leaks; visible but filed by someone else (the agent's
        owner, an ``org:admin``) raises ``forbidden``.
        """
        row = await HeldExecutionRepository.get_by_id(
            session,
            approval_id,
            filters=build_access_filters(identity, HeldExecution),
            for_update=for_update,
        )
        if row is None:
            raise HeldExecutionNotFoundError(approval_id)
        if row.created_by != identity.sub:
            raise forbidden(approval_id)
        return row

    async def withdraw(self, approval_id: str, *, identity: Identity) -> HeldExecutionView:
        """Withdraw a pending approval — only the identity that filed the hold.

        A compare-and-set on ``pending``: in one transaction the approval
        becomes ``withdrawn`` and its held job ``cancelled``, with no result
        written. A caller who cannot see the hold gets
        ``HeldExecutionNotFoundError``; one who can see it but did not file it
        (its owner, an ``org:admin``) gets ``HeldExecutionWithdrawForbiddenError``
        and denies it with ``decide`` instead. An approval that is no longer
        pending gets ``HeldExecutionAlreadyDecidedError``. The withdrawal is
        audit-logged and emits ``execution.approval_withdrawn``.
        """
        async with self._ctx.admin_db.transaction() as session:
            now = await HeldExecutionRepository.now(session)
            row = await self._require_filed(
                session, approval_id, identity, forbidden=HeldExecutionWithdrawForbiddenError
            )
            updated = await HeldExecutionRepository.withdraw(
                session, approval_id, created_by=identity.sub, now=now
            )
            if updated is None:
                state = await HeldExecutionRepository.get_state(session, approval_id)
                raise HeldExecutionAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state or row.state}"
                )
            moved = await JobRepository.transition(
                session,
                updated.job_id,
                from_status=JobStatus.HELD,
                to_status=JobStatus.CANCELLED,
            )
            if not moved:
                raise HeldExecutionJobNotHeldError(approval_id, updated.job_id)
            agent = await AgentRepository.get_by_id(session, updated.agent_id)
            await AuditRepository.record(
                session,
                action=AuditAction.WITHDRAW,
                target_type=AuditTargetType.HELD_EXECUTION,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"state": HeldExecutionState.PENDING.value},
                after={"state": HeldExecutionState.WITHDRAWN.value},
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_WITHDRAWN,
                severity=EventSeverity.INFO,
                summary=held_call_summary(
                    agent_name=agent.name if agent is not None else None,
                    agent_id=updated.agent_id,
                    method=updated.method,
                    path=updated.path,
                    outcome="withdrawn",
                ),
                job_id=updated.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "held_execution_id": approval_id,
                    "agent_id": updated.agent_id,
                    "method": updated.method,
                    "path": updated.path,
                },
            )
            view = HeldExecutionView.model_validate(updated).model_copy(
                update={
                    "execution_status": JobStatus.CANCELLED.value,
                }
            )
        logger.info(
            "held_execution_withdrawn",
            approval_id=approval_id,
            job_id=view.job_id,
            actor_id=identity.sub,
        )
        return view

    async def explain(
        self, approval_id: str, reason: str, *, identity: Identity
    ) -> HeldExecutionView:
        """Record why the filing agent made a pending held call — once.

        Only the identity that filed the hold may explain it. A caller who cannot
        see the hold gets ``HeldExecutionNotFoundError``; one who can see it but
        did not file it (its owner, an ``org:admin``) gets
        ``HeldExecutionExplainForbiddenError``. A hold that is no longer
        pending, or past its window, gets
        ``HeldExecutionAlreadyDecidedError``; one already explained gets
        ``HeldExecutionAlreadyExplainedError``, so a reviewer never sees the
        reason change under them: the reason is written only while
        ``agent_reason`` is empty. The text is cleaned first
        (``clean_agent_text``); text with nothing readable left is
        ``InvalidInputError``. The explanation is audit-logged by its length,
        never its text, and emits ``execution.approval_reason_added``.

        The hold's row is locked for the transaction: a decision, withdrawal or
        expiry in flight either lands first, and this answers "already
        settled", or waits until the explanation is in.
        """
        text = clean_agent_text(reason)
        if text is None:
            raise InvalidInputError("The reason has no readable text")
        async with self._ctx.admin_db.transaction() as session:
            now = await HeldExecutionRepository.now(session)
            row = await self._require_filed(
                session,
                approval_id,
                identity,
                forbidden=HeldExecutionExplainForbiddenError,
                for_update=True,
            )
            if row.state != HeldExecutionState.PENDING or row.expires_at <= now:
                state = (
                    row.state
                    if row.state != HeldExecutionState.PENDING
                    else HeldExecutionState.EXPIRED.value
                )
                raise HeldExecutionAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state}"
                )
            if not await HeldExecutionRepository.set_agent_reason(
                session, approval_id, reason=text
            ):
                raise HeldExecutionAlreadyExplainedError(approval_id)
            statuses = await JobRepository.held_execution_statuses(
                session, [row.job_id], filters=build_access_filters(identity, Job)
            )
            agent = await AgentRepository.get_by_id(session, row.agent_id)
            await AuditRepository.record(
                session,
                action=AuditAction.EXPLAIN,
                target_type=AuditTargetType.HELD_EXECUTION,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"reason_length": len(text)},
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_REASON_ADDED,
                severity=EventSeverity.INFO,
                summary=held_call_summary(
                    agent_name=agent.name if agent is not None else None,
                    agent_id=row.agent_id,
                    method=row.method,
                    path=row.path,
                    outcome="explained why",
                ),
                job_id=row.job_id,
                created_by=row.agent_id,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={"held_execution_id": approval_id, "agent_id": row.agent_id},
            )
            view = HeldExecutionView.model_validate(row).model_copy(
                update={
                    "execution_status": _execution_status(statuses.get(row.job_id)),
                    "agent_reason": text,
                }
            )
        logger.info(
            "held_execution_explained",
            approval_id=approval_id,
            actor_id=identity.sub,
            reason_length=len(text),
        )
        return view
