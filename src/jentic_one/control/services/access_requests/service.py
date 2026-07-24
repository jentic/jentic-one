"""AccessRequestService — lifecycle orchestration for access requests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

import structlog

from jentic_one.control.core.errors import DuplicatePendingItemError
from jentic_one.control.core.schema.access_request_items import RULE_BEARING_COMBINATIONS
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.repos.access_request_repo import AccessRequestRepository
from jentic_one.control.repos.credential_repo import CredentialRepository
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.control.repos.toolkit_repo import ToolkitRepository
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.access_requests.effects import (
    EffectApplicator,
    EffectPhase,
    admin_effect_keys,
    classify_effect,
)
from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    AdminEffectReconcileError,
    CredentialNotFoundForBindError,
    DuplicatePendingError,
    ItemNotOnRequestError,
    ItemNotPendingError,
    NotAReviewerError,
    PrerequisiteNotMetError,
    ProvisioningPlanNotFulfilledError,
    RequestNotPendingError,
    RequiredFieldMissingError,
    RulesNotSupportedForBindError,
    ToolkitNotVisibleError,
    ToolkitReferenceUnresolvedError,
    assert_grantable_scope,
)
from jentic_one.control.services.access_requests.schemas.access_requests import (
    AccessRequestItemView,
    AccessRequestPage,
    AccessRequestView,
    CollectedResourceIds,
    Evaluation,
    EvaluationCheck,
    ResolvedNames,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event
from jentic_one.shared.models import (
    AccessRequestItemStatus,
    AccessRequestStatus,
)
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor
from jentic_one.shared.scopes import AGENTS_WRITE, ORG_ADMIN

logger = structlog.get_logger(__name__)

# validate() failures that mean an approved item can never be fulfilled as filed:
# the bind target does not exist or is not visible to the approver. decide()
# converts these into a DENY-with-reason (instead of letting validate() raise,
# which rolls the whole control-DB transaction back and strands the request as
# PENDING) so the loop closes — the agent's `jentic access request --wait`
# resolves to `denied` with an actionable message rather than timing out blind.
# Malformed-item failures (rules on a toolkit:bind, a non-grantable scope) and an
# *ambiguous* reference are intentionally excluded: those still raise so the
# request stays pending while the operator/agent fixes or amends it. See #696
# (loop never closes) and #658 (can_fulfill doesn't model fulfillability).
_UNFULFILLABLE_BIND_TARGET: tuple[type[Exception], ...] = (
    ToolkitReferenceUnresolvedError,
    ToolkitNotVisibleError,
    CredentialNotFoundForBindError,
    RequiredFieldMissingError,
    ProvisioningPlanNotFulfilledError,
)

# The fulfilment-only intent item types that mark a request as a provisioning
# plan. Their presence means the bind items are fulfilled out-of-band by the
# setup wizard (create toolkit + credential, then amend their ids), so a plain
# approval of an unfulfilled bind must be denied with a plan-aware reason rather
# than half-approving into a guaranteed-broken state.
_PLAN_INTENT_COMBINATIONS: frozenset[tuple[str, str]] = frozenset(
    {("toolkit", "create"), ("credential", "provision")}
)


class AccessRequestService:
    """Style A standalone service for access request lifecycle operations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._effects = EffectApplicator(ctx)

    @staticmethod
    def _reject_unsupported_rules(resource_type: str, action: str, rules: Any) -> None:
        """Reject permission rules attached to an item type that cannot enforce them.

        Raised before any DB write so a non-enforceable allowlist is never persisted
        (file) or stitched onto a stored item (amend). The rule-bearing allowlist
        (``RULE_BEARING_COMBINATIONS``) is shared with the repo's default-rule
        substitution so the two never disagree. See ``RulesNotSupportedForBindError``
        for the structural reason.
        """
        if rules and (resource_type, action) not in RULE_BEARING_COMBINATIONS:
            raise RulesNotSupportedForBindError(resource_type, action)

    @staticmethod
    def _validate_grantable_scope(item: dict[str, Any]) -> None:
        """Reject a scope:grant for a scope outside the self-service allow-list.

        Validating at file time (in addition to the decide-time guard in the
        effect applicator) stops an agent from filing a request for a phantom
        scope and getting a misleading "approved" back for a grant that could
        never take effect. See issue #672.
        """
        if item.get("resource_type") != "scope" or item.get("action") != "grant":
            return
        scope = item.get("resource_id")
        assert_grantable_scope(scope if isinstance(scope, str) else None)

    async def _check_prerequisite(self, actor_id: str, resource_type: str, to_id: str) -> None:
        """Verify that the actor has the required binding for the resource type."""
        if resource_type == "credential":
            async with self._ctx.admin_db.session() as session:
                bound = await PrerequisiteRepository.agent_toolkit_binding_exists(
                    session, agent_id=actor_id, toolkit_id=to_id
                )
                if not bound:
                    raise PrerequisiteNotMetError(actor_id, to_id, resource_type)

    async def _emit(
        self,
        *,
        type: str,
        summary: str,
        request_id: str,
        status: str,
        created_by: str,
        actor_id: str | None = None,
        actor_type: str | None = None,
    ) -> None:
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event(
                    session,
                    type=type,
                    severity=EventSeverity.INFO,
                    summary=summary,
                    requires_action=(type == EventType.ACCESS_REQUEST_FILED),
                    data={"request_id": request_id, "status": status},
                    created_by=created_by,
                    actor_id=actor_id,
                    actor_type=actor_type,
                )
        except Exception:
            logger.warning("emit_event_failed", request_id=request_id, type=type, exc_info=True)

    async def file(
        self,
        *,
        actor_id: str,
        reason: str | None,
        items: list[dict[str, Any]],
        identity: Identity,
    ) -> AccessRequestView:
        """File a new access request."""
        for item in items:
            self._reject_unsupported_rules(item["resource_type"], item["action"], item.get("rules"))
            self._validate_grantable_scope(item)
            to_id = item.get("to_id")
            if to_id is not None:
                await self._check_prerequisite(actor_id, item["resource_type"], to_id)

        config = self._ctx.config.control.access_requests
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=config.ttl_days)
        created_by = identity.sub
        requested_by = identity.sub
        filer_owner_id = identity.parent_actor_id or identity.sub

        try:
            async with self._ctx.control_db.transaction() as session:
                for item in items:
                    duplicate = await AccessRequestRepository.find_pending_duplicate(
                        session,
                        actor_id=actor_id,
                        resource_type=item["resource_type"],
                        action=item["action"],
                        to_id=item.get("to_id"),
                        resource_id=item.get("resource_id"),
                        resource_reference=item.get("resource_reference"),
                    )
                    if duplicate is not None:
                        parent_request = duplicate.access_request
                        raise DuplicatePendingError(
                            approve_url=parent_request.approve_url if parent_request else "",
                            existing_request_id=(
                                parent_request.id if parent_request else duplicate.access_request_id
                            ),
                        )

                request = await AccessRequestRepository.create(
                    session,
                    actor_id=actor_id,
                    reason=reason,
                    requested_by=requested_by,
                    approve_url="",
                    expires_at=expires_at,
                    items=items,
                    created_by=created_by,
                    filer_owner_id=filer_owner_id,
                )

                approve_url = f"{config.canonical_base_url}/access-requests/{request.id}"
                request.approve_url = approve_url
                await session.flush()

                names = await self._resolve_names(session, [request])
                view = self._to_view(request, names=names)
        except DuplicatePendingItemError as exc:
            async with self._ctx.control_db.session() as session:
                for item in items:
                    dup = await AccessRequestRepository.find_pending_duplicate(
                        session,
                        actor_id=actor_id,
                        resource_type=item["resource_type"],
                        action=item["action"],
                        to_id=item.get("to_id"),
                        resource_id=item.get("resource_id"),
                        resource_reference=item.get("resource_reference"),
                    )
                    if dup is not None:
                        parent = dup.access_request
                        raise DuplicatePendingError(
                            approve_url=parent.approve_url if parent else "",
                            existing_request_id=(parent.id if parent else dup.access_request_id),
                        ) from exc
                raise

        await self._emit(
            type=EventType.ACCESS_REQUEST_FILED,
            summary=f"Access request filed by {requested_by}",
            request_id=view.id,
            status=view.status,
            created_by=created_by,
            actor_id=created_by,
            actor_type=identity.actor_type,
        )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.ACCESS_REQUEST,
            target_id=view.id,
            actor_type=identity.actor_type,
            actor_id=created_by,
            reason=reason,
            origin=identity.origin.value,
        )
        return view

    async def get(self, request_id: str, *, identity: Identity) -> AccessRequestView:
        """Get a single access request by ID with evaluation."""
        access_filters = build_access_filters(identity, AccessRequest)
        async with self._ctx.control_db.session() as session:
            request = await AccessRequestRepository.get(session, request_id, filters=access_filters)
            if request is None:
                raise AccessRequestNotFoundError(request_id)

            names = await self._resolve_names(session, [request])
            view = self._to_view(request, names=names)
            view.evaluation = self._compute_evaluation(request, identity)
            return view

    async def list_all(
        self,
        *,
        identity: Identity,
        actor_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AccessRequestPage:
        """List access requests with cursor pagination."""
        access_filters = build_access_filters(identity, AccessRequest)

        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        async with self._ctx.control_db.session() as session:
            rows = await AccessRequestRepository.list_all(
                session,
                actor_id=actor_id,
                status=status,
                cursor=decoded_cursor,
                limit=limit,
                filters=access_filters,
            )

            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]

            names = await self._resolve_names(session, rows)
            data = [self._to_view(r, names=names) for r in rows]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = encode_cursor(last.filed_at, last.id)

        return AccessRequestPage(data=data, has_more=has_more, next_cursor=next_cursor)

    async def decide(
        self,
        request_id: str,
        *,
        identity: Identity,
        item_decisions: Sequence[dict[str, Any]],
    ) -> AccessRequestView:
        """Apply decisions (approve/deny) to individual items on a request.

        The decision itself and the in-control credential-bind effects are applied
        atomically in a single control-DB transaction (phase 1). Admin-DB effects
        (toolkit bind, scope grant) cannot share that transaction, so they are
        applied **after** the control commit (phase 2) and acked back into the
        control DB. Because an approved admin-effect item with ``applied_effects IS
        NULL`` is the un-acked marker, ``decide()`` is **safe to call repeatedly**
        with the same ``item_decisions``: a re-call reconciles any un-acked admin
        effect (idempotent via ON CONFLICT) instead of erroring, and a genuine
        conflict (e.g. requested DENY for an already-APPROVED item) still raises
        ``ItemNotPendingError``. This makes the cross-DB write provably
        reconcilable on retry with no orphaned admin-DB bindings/grants.

        Before any admin-DB write, every approved item is ``validate()``-d: a
        freshly-approved (PENDING) item is validated inside phase 1 before its
        decision is committed — a malformed/ambiguous/non-grantable target raises
        and aborts the whole decision, while an unfulfillable bind target (see
        ``_UNFULFILLABLE_BIND_TARGET``) is instead recorded as a DENY-with-reason
        so the loop closes; an already-APPROVED item on the reconcile path is
        re-validated before its admin effect is re-driven. The admin-DB writes are
        idempotent (``ON CONFLICT DO NOTHING``) so a retry after a partial failure
        converges.
        """
        decided_by = identity.sub
        access_filters = build_access_filters(identity, AccessRequest)

        pending_admin_ids: list[str] = []
        any_transition = False

        async with self._ctx.control_db.transaction() as session:
            request = await AccessRequestRepository.get(session, request_id, filters=access_filters)
            if request is None:
                raise AccessRequestNotFoundError(request_id)

            evaluation = self._compute_evaluation(request, identity)
            if not evaluation.can_fulfill:
                raise NotAReviewerError(request_id)

            items_by_id = {item.id: item for item in request.items}
            # A request is a provisioning plan when it carries fulfilment intents;
            # its bind items are only satisfiable after the wizard fulfils them, so
            # validate() denies an unfulfilled bind with a plan-aware reason.
            is_plan = any(
                (it.resource_type, it.action) in _PLAN_INTENT_COMBINATIONS for it in request.items
            )
            control_effect_items: list[tuple[str, Any]] = []

            for decision in item_decisions:
                item_id = decision["item_id"]
                existing = items_by_id.get(item_id)
                if existing is None:
                    raise ItemNotOnRequestError(item_id, request_id)
                verdict = decision["decision"]
                reason = decision.get("decision_reason")

                if verdict == AccessRequestItemStatus.APPROVED:
                    # Defense-in-depth: every item's actor is forced to the filer's
                    # own subject at file time, and the approval gate ties the
                    # decider to the filer's owner. Assert that invariant locally so
                    # a future change to the file/decide path can't let an approver
                    # grant a scope or binding to an actor outside the request's own
                    # actor. (`existing.actor_id` == the eventual target's; checking
                    # here covers both the fresh-approve and reconcile paths.)
                    if existing.actor_id != request.actor_id:
                        raise NotAReviewerError(request_id)

                    if existing.status == AccessRequestItemStatus.PENDING:
                        # Validate the bind target before committing the approval.
                        # If it is unfulfillable as filed, close the loop by
                        # recording a DENY with the failure as the reason instead of
                        # letting validate() raise (which would roll the whole
                        # transaction back and strand the request as PENDING — the
                        # agent's `--wait` would then time out, never learning why).
                        # See _UNFULFILLABLE_BIND_TARGET / #696.
                        try:
                            await self._effects.validate(
                                existing,
                                identity=identity,
                                control_session=session,
                                is_provisioning_plan=is_plan,
                            )
                        except _UNFULFILLABLE_BIND_TARGET as exc:
                            verdict = AccessRequestItemStatus.DENIED
                            reason = str(exc)

                result = await AccessRequestRepository.decide_item(
                    session,
                    item_id,
                    verdict,
                    decided_by=decided_by,
                    decision_reason=reason if verdict == AccessRequestItemStatus.DENIED else None,
                )
                if result is None:
                    # Item was not PENDING. This is a retry/reconcile if it already
                    # holds the requested terminal state; otherwise it is a genuine
                    # conflict and must surface as an error.
                    if existing.status != verdict:
                        raise ItemNotPendingError(item_id, existing.status)
                    target = existing
                else:
                    any_transition = True
                    target = result

                if verdict != AccessRequestItemStatus.APPROVED:
                    continue

                # Reconcile path only: the item was already APPROVED by a prior
                # decide() whose admin effect didn't ack, so re-validate to drive
                # the idempotent retry. A freshly-approved item (result is not None)
                # was already validated above (and an unfulfillable one was flipped
                # to DENY and skipped via the `continue`).
                if result is None:
                    await self._effects.validate(
                        target,
                        identity=identity,
                        control_session=session,
                        is_provisioning_plan=is_plan,
                    )

                phase = classify_effect(target.resource_type, target.action)
                if phase is EffectPhase.ADMIN:
                    # Leave applied_effects = NULL (the un-acked marker) and drive
                    # the admin write after the control commit (phase 2).
                    pending_admin_ids.append(item_id)
                elif target.applied_effects is None:
                    # Control-session effect (credential bind) or skipped: apply it
                    # inline so it is atomic with the decision. Only do so once —
                    # a populated applied_effects means it was already acked.
                    control_effect_items.append((item_id, target))

            for item_id, item_result in control_effect_items:
                effect = await self._effects.apply(
                    item_result, identity=identity, control_session=session
                )
                effects_dict = effect.model_dump()
                await AccessRequestRepository.set_applied_effects(session, item_id, effects_dict)
                logger.info("access_request_effects_applied", item_id=item_id, effects=effects_dict)

        # Phase 1 is now durable. Reconcile (drive + ack) the admin effects: both
        # the items collected above and any approved admin item still un-acked from
        # a prior partial run.
        await self._reconcile_admin_effects(
            request_id, identity=identity, extra_item_ids=pending_admin_ids
        )

        async with self._ctx.control_db.session() as session:
            refreshed = await AccessRequestRepository.get(
                session, request_id, filters=access_filters
            )
            assert refreshed is not None
            names = await self._resolve_names(session, [refreshed])
            view = self._to_view(refreshed, names=names)

        if any_transition:
            await self._emit_decision(view, decided_by=decided_by, identity=identity)
        return view

    async def _emit_decision(
        self, view: AccessRequestView, *, decided_by: str, identity: Identity
    ) -> None:
        """Emit the decision event + audit record for a decide() that transitioned items."""
        if view.status in (
            AccessRequestStatus.APPROVED,
            AccessRequestStatus.PARTIALLY_APPROVED,
        ):
            event_type: str | None = EventType.ACCESS_REQUEST_APPROVED
        elif view.status == AccessRequestStatus.DENIED:
            event_type = EventType.ACCESS_REQUEST_DENIED
        else:
            event_type = None

        if event_type is not None:
            await self._emit(
                type=event_type,
                summary=f"Access request {view.status} by {decided_by}",
                request_id=view.id,
                status=view.status,
                created_by=decided_by,
                actor_id=decided_by,
                actor_type=identity.actor_type,
            )
        audit_action = (
            AuditAction.DENY if view.status == AccessRequestStatus.DENIED else AuditAction.APPROVE
        )
        await record_audit_best_effort(
            self._ctx,
            action=audit_action,
            target_type=AuditTargetType.ACCESS_REQUEST,
            target_id=view.id,
            actor_type=identity.actor_type,
            actor_id=decided_by,
            after={"status": view.status},
            origin=identity.origin.value,
        )

    async def _reconcile_admin_effects(
        self,
        request_id: str,
        *,
        identity: Identity,
        extra_item_ids: Sequence[str] = (),
    ) -> None:
        """Drive + ack all un-acked admin-DB effects for a request (phase 2).

        Each admin effect resolves/authorizes its target on a fresh control-DB
        session (the toolkit-reference/visibility check) and opens its own admin-DB
        transaction (idempotent via ON CONFLICT); after a successful apply we record
        the ack in a short control transaction. A single item's failure does not
        abort the pass — every other item still gets driven and acked, and any item
        left un-acked remains reconcilable on the next decide() call. If any item
        failed, we re-raise afterwards so the caller learns the decision is
        incomplete and should retry; the already-committed decision and the
        already-acked items are preserved.

        This helper is the single implementation shared by the inline phase-2 path
        and any future reconcile/sweeper entry point.
        """
        async with self._ctx.control_db.session() as session:
            unacked = await AccessRequestRepository.list_unacked_admin_effect_items(
                session, request_id, admin_effect_keys=admin_effect_keys()
            )

        items_by_id = {item.id: item for item in unacked}
        # Preserve the caller-provided order, then append any extra un-acked items.
        ordered_ids = list(dict.fromkeys([*extra_item_ids, *items_by_id.keys()]))

        failures: list[str] = []
        for item_id in ordered_ids:
            item = items_by_id.get(item_id)
            if item is None:
                # Already acked (or no longer un-acked) by the time we got here.
                continue
            try:
                async with self._ctx.control_db.session() as session:
                    effect = await self._effects.apply(
                        item, identity=identity, control_session=session
                    )
                effects_dict = effect.model_dump()
                async with self._ctx.control_db.transaction() as session:
                    await AccessRequestRepository.set_applied_effects(
                        session, item_id, effects_dict
                    )
                logger.info("access_request_effects_applied", item_id=item_id, effects=effects_dict)
            except Exception:
                logger.warning(
                    "access_request_admin_effect_failed",
                    request_id=request_id,
                    item_id=item_id,
                    exc_info=True,
                )
                failures.append(item_id)

        if failures:
            raise AdminEffectReconcileError(request_id, failures)

    async def amend(
        self,
        request_id: str,
        *,
        identity: Identity,
        item_amendments: Sequence[dict[str, Any]],
    ) -> AccessRequestView:
        """Amend pending items on an access request (update rules or resource_id)."""
        access_filters = build_access_filters(identity, AccessRequest)

        async with self._ctx.control_db.transaction() as session:
            request = await AccessRequestRepository.get(session, request_id, filters=access_filters)
            if request is None:
                raise AccessRequestNotFoundError(request_id)

            if request.status != AccessRequestStatus.PENDING:
                raise RequestNotPendingError(request_id, request.status)

            items_by_id = {item.id: item for item in request.items}
            for amendment in item_amendments:
                item_id = amendment["item_id"]
                target = items_by_id.get(item_id)
                if target is None:
                    raise ItemNotOnRequestError(item_id, request_id)
                # Closing the back door: rules cannot be amended onto an item type
                # that can't enforce them (e.g. a toolkit:bind), only filed types.
                self._reject_unsupported_rules(
                    target.resource_type, target.action, amendment.get("rules")
                )
                # A scope:grant's resource_id can be amended; re-run the same
                # file-time allow-list guard so an amendment can't park a
                # privileged/phantom scope on a pending item. Decide-time still
                # rejects it, but #672 also guarantees no misleading-pending
                # state. Only validate when resource_id is actually being changed;
                # an amendment that touches only rules leaves it as filed.
                new_resource_id = amendment.get("resource_id")
                if new_resource_id is not None:
                    self._validate_grantable_scope(
                        {
                            "resource_type": target.resource_type,
                            "action": target.action,
                            "resource_id": new_resource_id,
                        }
                    )
                result = await AccessRequestRepository.amend_item(
                    session,
                    item_id,
                    rules=amendment.get("rules"),
                    resource_id=amendment.get("resource_id"),
                    to_id=amendment.get("to_id"),
                )
                if result is None:
                    raise ItemNotPendingError(item_id, "unknown")

            refreshed = await AccessRequestRepository.get(session, request_id)
            assert refreshed is not None
            names = await self._resolve_names(session, [refreshed])
            view = self._to_view(refreshed, names=names)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.ACCESS_REQUEST,
            target_id=view.id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            reason="amend pending items",
            origin=identity.origin.value,
        )
        return view

    async def withdraw(self, request_id: str, *, identity: Identity) -> AccessRequestView:
        """Withdraw a pending access request."""
        access_filters = build_access_filters(identity, AccessRequest)

        async with self._ctx.control_db.transaction() as session:
            request = await AccessRequestRepository.get(session, request_id, filters=access_filters)
            if request is None:
                raise AccessRequestNotFoundError(request_id)

            if request.status != AccessRequestStatus.PENDING:
                raise RequestNotPendingError(request_id, request.status)

            withdrawn = await AccessRequestRepository.withdraw(session, request_id)
            assert withdrawn is not None
            names = await self._resolve_names(session, [withdrawn])
            view = self._to_view(withdrawn, names=names)

        await self._emit(
            type=EventType.ACCESS_REQUEST_WITHDRAWN,
            summary="Access request withdrawn",
            request_id=view.id,
            status=view.status,
            created_by=identity.sub,
            actor_id=identity.sub,
            actor_type=identity.actor_type,
        )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.REVOKE,
            target_type=AuditTargetType.ACCESS_REQUEST,
            target_id=view.id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            reason="withdrawn",
            origin=identity.origin.value,
        )
        return view

    @staticmethod
    def _collect_resource_ids(
        request: AccessRequest,
    ) -> CollectedResourceIds:
        """Extract unique toolkit and credential IDs from access request items."""
        toolkit_ids: set[str] = set()
        credential_ids: set[str] = set()
        for item in request.items:
            if item.resource_type == "toolkit" and item.resource_id:
                toolkit_ids.add(item.resource_id)
            elif item.resource_type == "credential" and item.resource_id:
                credential_ids.add(item.resource_id)
            if item.to_type == "toolkit" and item.to_id:
                toolkit_ids.add(item.to_id)
        return CollectedResourceIds(
            toolkit_ids=list(toolkit_ids), credential_ids=list(credential_ids)
        )

    async def _resolve_names(
        self,
        session: Any,
        requests: list[AccessRequest],
    ) -> ResolvedNames:
        """Batch-resolve toolkit/credential names for a list of access requests."""
        toolkit_ids: set[str] = set()
        credential_ids: set[str] = set()
        for request in requests:
            collected = self._collect_resource_ids(request)
            toolkit_ids.update(collected.toolkit_ids)
            credential_ids.update(collected.credential_ids)

        toolkit_names: dict[str, str] = {}
        credential_names: dict[str, str] = {}
        if toolkit_ids:
            toolkit_names = await ToolkitRepository.get_names_by_ids(session, list(toolkit_ids))
        if credential_ids:
            credential_names = await CredentialRepository.get_names_by_ids(
                session, list(credential_ids)
            )
        return ResolvedNames(toolkit_names=toolkit_names, credential_names=credential_names)

    def _to_view(
        self,
        request: AccessRequest,
        *,
        names: ResolvedNames | None = None,
    ) -> AccessRequestView:
        """Convert an ORM AccessRequest to its Pydantic view model."""
        status = request.status
        if status == AccessRequestStatus.PENDING and request.expires_at < dt.datetime.now(dt.UTC):
            status = AccessRequestStatus.EXPIRED

        resolved = names or ResolvedNames()
        tk_names = resolved.toolkit_names
        cred_names = resolved.credential_names

        item_views = [
            AccessRequestItemView(
                id=item.id,
                resource_type=item.resource_type,
                action=item.action,
                resource_id=item.resource_id,
                resource_reference=item.resource_reference,
                to_type=item.to_type,
                to_id=item.to_id,
                toolkit_name=(
                    tk_names.get(item.resource_id or "")
                    if item.resource_type == "toolkit"
                    else tk_names.get(item.to_id or "")
                    if item.to_type == "toolkit"
                    else None
                ),
                credential_name=(
                    cred_names.get(item.resource_id or "")
                    if item.resource_type == "credential"
                    else None
                ),
                rules=item.rules,
                status=item.status,
                applied_effects=item.applied_effects,
                decided_by=item.decided_by,
                decided_at=item.decided_at,
                decision_reason=item.decision_reason,
            )
            for item in request.items
        ]

        return AccessRequestView(
            id=request.id,
            actor_id=request.actor_id,
            reason=request.reason,
            requested_by=request.requested_by,
            status=status,
            approve_url=request.approve_url,
            filed_at=request.filed_at,
            expires_at=request.expires_at,
            created_by=request.created_by,
            filer_owner_id=request.filer_owner_id,
            items=item_views,
        )

    def _compute_evaluation(self, request: AccessRequest, identity: Identity) -> Evaluation:
        """Compute whether the caller can fulfill the request."""
        checks: list[EvaluationCheck] = []

        not_filer = identity.sub != request.created_by
        checks.append(
            EvaluationCheck(
                check="not_filer",
                passed=not_filer,
                blocker="Cannot approve own request" if not not_filer else None,
            )
        )

        has_agents_write = AGENTS_WRITE in identity.permissions or ORG_ADMIN in identity.permissions
        checks.append(
            EvaluationCheck(
                check="agents_write_scope",
                passed=has_agents_write,
                blocker="Missing agents:write permission" if not has_agents_write else None,
            )
        )

        is_admin = ORG_ADMIN in identity.permissions
        owns_filer = identity.sub == request.filer_owner_id or is_admin
        checks.append(
            EvaluationCheck(
                check="owns_filer",
                passed=owns_filer,
                blocker="Caller does not own the filing agent" if not owns_filer else None,
            )
        )

        can_fulfill = all(c.passed for c in checks)
        return Evaluation(can_fulfill=can_fulfill, checks=checks)
