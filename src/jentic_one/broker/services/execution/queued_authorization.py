"""Worker-time authorization for queued (``Prefer: respond-async``) executions.

The sync execute route authorizes a request against the state of the world at
request time. A queued execution runs later, on the worker — so it is
re-authorized there, with the **same** policy (:func:`authorize_execution`),
before any credential is resolved:

1. the enqueuing actor must still be active (the sync path's token-resolution
   check, answered from the actor row since the worker holds no token);
2. an agent must still hold the execute scope (the sync path's
   ``require_execute_scope``, answered from the live grant rows an agent's
   credentials resolve their scopes from);
3. the actor's bindings for the API are re-derived, so a removed or
   suspended binding (or a disabled credential) no longer resolves;
4. the binding's permission rules are re-evaluated for the operation.

A denial becomes a :class:`QueuedExecutionVerdict` carrying the problem body
the sync route would have returned; the worker records it and never injects.
Implements the shared ``ExecutionAuthorizer`` protocol.
"""

from __future__ import annotations

from urllib.parse import urlparse

import structlog

from jentic_one.broker.core.exceptions import BrokerError, OperationNotFoundError
from jentic_one.broker.core.problem import broker_error_problem
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.broker.services.execution.authorization import authorize_execution
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    AgentRuleEvaluatorProtocol,
    CredentialDeriverProtocol,
    RuleVerdict,
)
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.hold import get_approved_by_job_id
from jentic_one.shared.jobs.protocols import QueuedExecutionRequest, QueuedExecutionVerdict
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

logger = structlog.get_logger(__name__)

# Mirrors the sync edge's 401 for a caller whose actor is no longer active
# (``require_broker_identity`` → ``Unauthorized(type="unauthorized")``).
_INACTIVE_ACTOR_DETAIL = "The actor that queued this execution is no longer active."


def _inactive_actor_problem(instance: str) -> dict[str, object]:
    return {
        "type": "unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "detail": _INACTIVE_ACTOR_DETAIL,
        "instance": instance,
    }


def _insufficient_scope_problem(instance: str) -> dict[str, object]:
    """Mirrors the sync edge's 403 from ``require_execute_scope``."""
    return {
        "type": "insufficient_scope",
        "title": "Forbidden",
        "status": 403,
        "detail": f"Insufficient scope: '{BROKER_EXECUTE_SCOPE}' required",
        "instance": instance,
    }


def _approval_denied_problem(instance: str) -> dict[str, object]:
    """Returned when a require-approval rule fires at run time with no approved row.

    A job that reaches the worker with a require-approval verdict and no
    approved execution_approvals row was never properly released — fail it
    with the same permission-denied shape as a sync denial.
    """
    return {
        "type": "action_denied",
        "title": "Forbidden",
        "status": 403,
        "detail": (
            "The execution was not approved. Approve or deny it via the "
            "execution approvals surface before the worker claims it."
        ),
        "instance": instance,
    }


def _broker_path(upstream_url: str) -> str:
    """The broker request path a sync call to ``upstream_url`` would have had.

    Used as the problem ``instance`` so a queued denial names the same
    occurrence the sync route's ``request.url.path`` would.
    """
    parsed = urlparse(upstream_url)
    return f"/{parsed.netloc}{parsed.path}"


class QueuedExecutionAuthorizer:
    """Re-runs the sync execute authorization for a queued job at run time."""

    def __init__(
        self,
        ctx: Context,
        *,
        actor_status: ActorStatusResolver,
        credential_deriver: CredentialDeriverProtocol,
        agent_rule_evaluator: AgentRuleEvaluatorProtocol,
    ) -> None:
        self._ctx = ctx
        self._actor_status = actor_status
        self._credential_deriver = credential_deriver
        self._agent_rule_evaluator = agent_rule_evaluator
        self._admin_db = ctx.admin_db

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        """Allow (with the current injection boundary) or deny (with a problem body)."""
        instance = _broker_path(request.upstream_url)

        if not await self._actor_status.is_active(
            actor_id=request.actor_id, actor_type=request.actor_type
        ):
            logger.info(
                "queued_execution_denied",
                reason="actor_inactive",
                actor_id=request.actor_id,
                actor_type=request.actor_type,
            )
            return QueuedExecutionVerdict(allowed=False, problem=_inactive_actor_problem(instance))

        if not await self._actor_status.holds_scope(
            actor_id=request.actor_id, actor_type=request.actor_type, scope=BROKER_EXECUTE_SCOPE
        ):
            logger.info(
                "queued_execution_denied",
                reason="insufficient_scope",
                actor_id=request.actor_id,
                actor_type=request.actor_type,
            )
            return QueuedExecutionVerdict(
                allowed=False, problem=_insufficient_scope_problem(instance)
            )

        if not (request.api_vendor and request.api_name and request.api_version):
            # Every enqueue carries the discovered (always concrete) API
            # identity; a payload without one cannot be re-authorized, and the
            # sync route never gets this far for an unregistered operation.
            return QueuedExecutionVerdict(
                allowed=False,
                problem=broker_error_problem(
                    OperationNotFoundError(
                        "Operation not found — unregistered upstream URL.",
                        type="operation_not_found",
                        instance=instance,
                    )
                ),
            )

        identity = Identity(
            sub=request.actor_id,
            actor_type=ActorType(request.actor_type),
            permissions=[],
            expires_at=None,
            active=True,
        )
        try:
            authorization = await authorize_execution(
                ctx=self._ctx,
                identity=identity,
                api=APIReference(
                    vendor=request.api_vendor,
                    name=request.api_name,
                    version=request.api_version,
                ),
                operation_id=request.operation_id,
                method=request.method,
                path=urlparse(request.upstream_url).path,
                instance=instance,
                credential_deriver=self._credential_deriver,
                agent_rule_evaluator=self._agent_rule_evaluator,
                credential_id=request.credential_id,
                request_server_variables=request.server_variables,
                server_variables_unresolved=request.server_variables_unresolved,
            )
        except BrokerError as exc:
            logger.info(
                "queued_execution_denied",
                reason=exc.type,
                actor_id=request.actor_id,
                actor_type=request.actor_type,
            )
            return QueuedExecutionVerdict(allowed=False, problem=broker_error_problem(exc))

        if authorization.verdict == RuleVerdict.REQUIRE_APPROVAL:
            # Rule still requires approval at run time. The job is only
            # allowed to proceed if a human reviewer approved it — look up
            # the execution_approvals row linked to this job.
            approved = False
            if request.job_id:
                async with self._admin_db.transaction() as session:
                    row = await get_approved_by_job_id(session, request.job_id)
                    approved = row is not None
            if not approved:
                logger.info(
                    "queued_execution_denied",
                    reason="require_approval_no_approved_row",
                    job_id=request.job_id,
                    actor_id=request.actor_id,
                    actor_type=request.actor_type,
                )
                return QueuedExecutionVerdict(
                    allowed=False, problem=_approval_denied_problem(instance)
                )
            logger.info(
                "queued_execution_approved_by_row",
                job_id=request.job_id,
                actor_id=request.actor_id,
                actor_type=request.actor_type,
            )
            cred = authorization.selected_credential.credential_id
            return QueuedExecutionVerdict(
                allowed=True,
                allowed_credential_ids=tuple(authorization.allowed_credential_ids),
                credential_id=cred,
            )

        return QueuedExecutionVerdict(
            allowed=True,
            allowed_credential_ids=tuple(authorization.allowed_credential_ids),
            # The credential the re-check just selected (the same one enqueue
            # picked, if it is still bound).
            credential_id=authorization.selected_credential.credential_id,
        )
