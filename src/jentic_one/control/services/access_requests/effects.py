"""Effect applicator — translates approved access-request items into authorization artifacts."""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.repos.credential_repo import CredentialRepository
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.control.repos.permission_rule_set_repo import PermissionRuleSetRepository
from jentic_one.control.scoping.filters import build_access_filters, credential_owner_scope
from jentic_one.control.services.access_requests.errors import (
    CredentialNotFoundForBindError,
    CredentialReferenceAmbiguousError,
    CredentialReferenceUnresolvedError,
    ProvisioningPlanNotFulfilledError,
    RequiredFieldMissingError,
    RuleSetNotFoundForBindError,
    RulesNotSupportedForBindError,
    RulesRequiredForBindError,
    UnsupportedAccessRequestItemError,
    assert_grantable_scope,
)
from jentic_one.control.services.access_requests.schemas.effects import (
    CredentialBindEffect,
    ScopeGrantEffect,
    SkippedEffect,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.access_requests import AccessRequestItemStatus
from jentic_one.shared.models.actors import actor_type_from_id
from jentic_one.shared.models.api_identity import slugify_api_field

logger = structlog.get_logger(__name__)

EffectResult = CredentialBindEffect | ScopeGrantEffect | SkippedEffect


# The fulfilment-only intent item type that marks a request as a provisioning
# plan. Exposed here (rather than only on the service) so ``plan_governance_for_items``
# — the pure function that computes which binds a plan governs — stays close
# to the phase table it consults. (Theme-5 Phase 3: ``toolkit:create`` is gone;
# a plan is now the 2-item ``credential:provision`` + ``credential:bind`` chain.)
PLAN_INTENT_COMBINATIONS: frozenset[tuple[str, str]] = frozenset({("credential", "provision")})

# Bind item combinations whose fulfilment contract flips inside a plan.
_PLAN_GOVERNABLE_BINDS: frozenset[tuple[str, str]] = frozenset({("credential", "bind")})

# Item statuses that keep an intent (or bind) "live" for the purposes of
# governance. A withdrawn/denied intent no longer defines a plan on a later
# decide() call.
_LIVE_ITEM_STATUSES: frozenset[str] = frozenset(
    {AccessRequestItemStatus.PENDING.value, AccessRequestItemStatus.APPROVED.value}
)


class _PlanGovernanceItem(Protocol):
    """Minimal read-only view of an access-request item for governance.

    ``plan_governance_for_items`` only inspects a handful of attributes; declaring
    that surface explicitly lets the tests pass ordinary duck-typed mocks
    without an ``AccessRequestItem`` construction.
    """

    id: str
    resource_type: str
    action: str
    resource_id: str | None
    resource_reference: dict[str, Any] | None
    status: str


def _canonical_api_key(reference: dict[str, Any] | None) -> tuple[str, str | None] | None:
    """Return the ``(vendor, name)`` slug key an item's reference targets.

    Version is deliberately excluded from the key: an intent filed against
    ``vendor/name`` wildcards every version of that API for governance purposes
    — a plan for ``sendgrid`` covers a bind for ``sendgrid@1``. Both axes are
    slugified via the shared identity seam so raw-domain references from the
    CLI (``httpbin.org``) match stored slug form (``httpbin-org``); see the
    same normalization in ``_resolve_toolkit_reference``.
    """
    if not reference:
        return None
    vendor = reference.get("vendor")
    if not vendor:
        return None
    raw_name = reference.get("name")
    return (
        slugify_api_field(str(vendor)),
        slugify_api_field(str(raw_name)) if raw_name else None,
    )


@dataclass(frozen=True, slots=True)
class PlanGovernance:
    """Why a bind item is governed by a provisioning plan.

    Carries the specific live intent item ids whose fulfilment the wizard
    must stamp before this bind can approve, and the canonical
    ``(vendor, name)`` slug key that made those intents relevant (``None``
    when the bind was governed conservatively — an unattributable reference).

    Default construction (``PlanGovernance()``) means "not governed by any
    plan" — the plain fulfilment contract applies. ``is_governed`` is the
    single decision point call-sites branch on; the id / API fields are for
    the diagnostic path (which intent must be fulfilled? which API tuple?).
    """

    governing_intent_ids: frozenset[str] = field(default_factory=frozenset)
    governing_api: tuple[str, str | None] | None = None

    @property
    def is_governed(self) -> bool:
        return bool(self.governing_intent_ids)


# Shared no-plan sentinel — cheap to reuse across every call-site and every
# non-governed bind item without re-allocating an empty dataclass instance.
# Exported (no leading underscore) so ``AccessRequestService.decide`` can pass
# it as the default when looking a bind up in a governance mapping.
UNGOVERNED_PLAN: PlanGovernance = PlanGovernance()


def plan_governance_for_items(
    items: Sequence[_PlanGovernanceItem],
) -> Mapping[str, PlanGovernance]:
    """Compute per-item plan governance for a request's bind items.

    A request is a *plan* iff it carries at least one live fulfilment-only
    intent (``credential:provision``). This function returns a mapping from
    bind ``item_id`` to :class:`PlanGovernance` for every ``credential:bind``
    whose fulfilment contract the plan flips — the bind can only be satisfied
    by the credential id stamped by the wizard (see
    ``EffectApplicator.validate``); a plain approval of such a bind denies
    with :class:`ProvisioningPlanNotFulfilledError`. Bind items not in the
    mapping are governed by the plain contract.

    Governance is per-item, not request-wide (issue #778):

    - A ``credential:bind`` is governed iff a live intent's canonical
      ``(vendor, name)`` matches the bind's ``resource_reference``. Version is
      not part of the key — an intent for ``vendor/name`` covers all versions.
      A bind for a different API is *not* governed and resolves normally by
      its reference (or its explicit ``resource_id``). A bind with an
      *unattributable* reference (missing or vendor-less) **and no explicit
      id** is governed conservatively: it can never resolve by reference, so
      the plan-aware denial beats the bare resolution error.

    Non-live intents (``denied`` / ``withdrawn``) do not govern — abandoning a
    plan reverts remaining binds to the plain contract on the next decide.

    The mapping value carries *which* live intent(s) govern this bind and
    *which* API tuple made them relevant — richer than a boolean so a
    diagnostic (an ``UNFULFILLABLE`` DENY reason, an operator dashboard row)
    can name the intent the wizard needs to fulfil rather than just "some plan
    somewhere".
    """
    live_intents = [
        it
        for it in items
        if (it.resource_type, it.action) in PLAN_INTENT_COMBINATIONS
        and (it.status in _LIVE_ITEM_STATUSES)
    ]
    if not live_intents:
        return {}

    intents_by_api: dict[tuple[str, str | None], list[str]] = {}
    untargeted_intent_ids: list[str] = []
    for it in live_intents:
        key = _canonical_api_key(it.resource_reference)
        if key is None:
            untargeted_intent_ids.append(it.id)
        else:
            intents_by_api.setdefault(key, []).append(it.id)

    governance: dict[str, PlanGovernance] = {}
    all_live_intent_ids = frozenset(it.id for it in live_intents)
    for item in items:
        key = (item.resource_type, item.action)
        if key not in _PLAN_GOVERNABLE_BINDS:
            continue
        if item.status not in _LIVE_ITEM_STATUSES:
            # An already-decided bind isn't going to be re-validated; excluding
            # it keeps the mapping to items decide() actually processes.
            continue
        bind_key = _canonical_api_key(item.resource_reference)
        if bind_key is None:
            if item.resource_id:
                # An explicit-id bind sharing a request with a plan resolves by
                # its id under the plain contract — it isn't the wizard's to
                # satisfy (it may have been stamped by a previous wizard pass).
                continue
            # An unattributable bind (no reference, or a vendor-less one) can
            # never resolve by reference — letting it fall to the plain
            # contract would surface a bare resolution error (a denial that
            # strands the operator without context) instead of a legible,
            # plan-aware denial. Treat it as governed by every live intent,
            # the conservative pre-#778 behaviour for malformed refs inside a
            # plan.
            governance[item.id] = PlanGovernance(governing_intent_ids=all_live_intent_ids)
            continue
        matching_intent_ids: list[str] = list(untargeted_intent_ids)
        if bind_key in intents_by_api:
            matching_intent_ids.extend(intents_by_api[bind_key])
        if matching_intent_ids:
            governance[item.id] = PlanGovernance(
                governing_intent_ids=frozenset(matching_intent_ids),
                governing_api=bind_key,
            )
    return governance


class EffectPhase(enum.Enum):
    """Which transaction phase applies an effect.

    ``ADMIN`` effects write to the admin DB in their own independent
    transaction and are applied after the control commit (reconcilable on
    retry). ``credential:bind`` is an ADMIN effect with a **control-first
    prologue** (theme-5 hard problem 6): its permission rules are committed to
    the control DB *before* the admin-DB binding row, so a crash between the
    two leaves inert rules, never a live rule-less bind. ``FULFILMENT_ONLY``
    items (``credential:provision``) are provisioning-plan placeholders: the
    applicator never mutates state for them — a human fulfils them out-of-band
    via the existing create endpoints — so approving one is a recorded no-op.
    ``UNSUPPORTED`` marks a retired/unknown ``(resource_type, action)`` pair;
    it is a **hard failure** at validate/apply time (Phase 3), never a silent
    skip — a Phase-3 server with a pre-Phase-5 client must fail loudly, not
    approve-and-grant-nothing.

    ``CONTROL_SESSION`` (an effect written atomically in the caller's
    control-DB transaction) currently has no members — the toolkit-era
    ``credential:bind`` was the last one — but the phase and its inline-apply
    path in ``decide()`` are kept for future single-DB effects.
    """

    CONTROL_SESSION = "control_session"
    ADMIN = "admin"
    FULFILMENT_ONLY = "fulfilment_only"
    UNSUPPORTED = "unsupported"


# Single source of truth for routing a (resource_type, action) pair to its phase.
# ``apply()`` and the service both consult this so the dispatch knowledge lives
# in one place. Retired pairs (toolkit:create, toolkit:bind) are intentionally
# absent: they classify as UNSUPPORTED and fail hard (see EffectPhase).
_EFFECT_PHASES: dict[tuple[str, str], EffectPhase] = {
    ("credential", "bind"): EffectPhase.ADMIN,
    ("scope", "grant"): EffectPhase.ADMIN,
    ("credential", "provision"): EffectPhase.FULFILMENT_ONLY,
}


def classify_effect(resource_type: str, action: str) -> EffectPhase:
    """Return the phase in which the effect for ``(resource_type, action)`` is applied."""
    return _EFFECT_PHASES.get((resource_type, action), EffectPhase.UNSUPPORTED)


def is_admin_effect(item: AccessRequestItem) -> bool:
    """True when the item's effect is applied in a separate admin-DB transaction."""
    return classify_effect(item.resource_type, item.action) is EffectPhase.ADMIN


def admin_effect_keys() -> tuple[tuple[str, str], ...]:
    """Return all ``(resource_type, action)`` pairs applied as admin-DB effects."""
    return tuple(key for key, phase in _EFFECT_PHASES.items() if phase is EffectPhase.ADMIN)


@dataclass(frozen=True, slots=True)
class PreparedEffect:
    """Control-phase output carried from ``prepare()`` to ``complete()``.

    For ``credential:bind`` it records the resolved credential id and how many
    inline rules were written in the (already committed) control transaction;
    other effects carry nothing.
    """

    credential_id: str | None = None
    rules_applied: int = 0


class EffectApplicator:
    """Applies authorization effects for approved access-request items.

    Admin-DB effects are applied in two stages (theme-5 hard problem 6):

    1. :meth:`prepare` — runs inside a caller-owned **control** transaction;
       resolves/authorizes the target and writes the control-DB half of the
       effect (a ``credential:bind``'s permission rules). The caller commits
       this transaction before stage 2, so the policy is durable first.
    2. :meth:`complete` — writes the admin-DB half (the binding row / scope
       grant) in its own transaction, idempotently.

    A crash between the stages leaves inert rules — never a live rule-less
    bind — and the un-acked ``applied_effects IS NULL`` marker makes the item
    reconcilable on the next ``decide()``. :meth:`apply` remains the
    single-shot entry point for fulfilment-only intents (a recorded no-op).
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def apply(
        self,
        item: AccessRequestItem,
        *,
        identity: Identity,
        control_session: Any,
    ) -> EffectResult:
        """Apply a non-admin effect inline (fulfilment-only intents).

        Admin-phase effects must go through :meth:`prepare`/:meth:`complete`
        (write-ordering matters — see the class docstring); passing one here is
        a programming error. Retired/unknown pairs fail loudly (Phase 3): the
        old ``UNSUPPORTED`` silent skip would approve-and-grant-nothing — the
        "hollow yes".
        """
        del identity, control_session  # dispatch parity with prepare/complete.
        phase = classify_effect(item.resource_type, item.action)

        if phase is EffectPhase.FULFILMENT_ONLY:
            # A provisioning-plan placeholder (credential:provision). The
            # applicator never mutates state for these — a human fulfils them
            # via the existing create endpoints and writes the resulting id onto
            # the downstream bind item (amend). Record an explicit, non-null
            # skipped effect so an approved intent is an audited no-op rather
            # than a silent one.
            return SkippedEffect(
                reason=(
                    f"fulfilment-only intent {item.resource_type}:{item.action} "
                    "is provisioned out-of-band; no effect applied"
                ),
            )
        if phase is EffectPhase.ADMIN:
            raise ValueError(
                f"admin effect {item.resource_type}:{item.action} must be applied "
                f"via prepare()/complete(), item={item.id}"
            )

        # Retired/unknown pair: fail loudly (Phase 3) — approving it would be a
        # recorded no-op the operator believes granted (the "hollow yes").
        logger.warning(
            "unsupported_effect_combination",
            resource_type=item.resource_type,
            action=item.action,
            item_id=item.id,
        )
        raise UnsupportedAccessRequestItemError(item.resource_type, item.action)

    async def prepare(
        self,
        item: AccessRequestItem,
        *,
        identity: Identity,
        control_session: Any,
    ) -> PreparedEffect:
        """Stage 1 of an admin effect: control-DB resolution + policy write.

        Must run inside a control **transaction** the caller commits before
        calling :meth:`complete` — for ``credential:bind`` this writes the
        binding's inline permission rules (rules-first; an attached
        ``rule_set_id`` needs no control write, the pointer rides on the
        admin row). Idempotent on retry: the rules write is a full replace.
        """
        key = (item.resource_type, item.action)
        if key == ("credential", "bind"):
            credential_id = await self._resolve_credential_bind_target(
                item, identity=identity, session=control_session
            )
            if not item.rules and not item.rule_set_id:
                # validate() already guards this; keep the invariant on the
                # reconcile path too so a rule-less bind can never be written.
                raise RulesRequiredForBindError()
            rules_applied = 0
            if item.rule_set_id is None and item.rules:
                await AgentPermissionRuleRepository.replace_user_rules(
                    control_session,
                    item.actor_id,
                    credential_id,
                    item.rules,
                    created_by=identity.sub,
                )
                rules_applied = len(item.rules)
            return PreparedEffect(credential_id=credential_id, rules_applied=rules_applied)
        if key == ("scope", "grant"):
            assert_grantable_scope(item.resource_id)
            return PreparedEffect()
        raise UnsupportedAccessRequestItemError(item.resource_type, item.action)

    async def complete(
        self,
        item: AccessRequestItem,
        *,
        identity: Identity,
        prepared: PreparedEffect,
    ) -> EffectResult:
        """Stage 2 of an admin effect: the admin-DB write, in its own transaction.

        Only safe to call after the :meth:`prepare` transaction committed —
        the write order (rules durable before the bind exists) is the whole
        point. Idempotent via ON CONFLICT DO NOTHING.
        """
        key = (item.resource_type, item.action)
        if key == ("credential", "bind"):
            return await self._complete_credential_bind(
                item, decided_by=identity.sub, prepared=prepared
            )
        if key == ("scope", "grant"):
            return await self._apply_scope_grant(item, decided_by=identity.sub)
        raise UnsupportedAccessRequestItemError(item.resource_type, item.action)

    async def validate(
        self,
        item: AccessRequestItem,
        *,
        identity: Identity,
        control_session: Any,
        plan_governance: PlanGovernance = UNGOVERNED_PLAN,
    ) -> None:
        """Validate an approved item's effect can be applied — without writing.

        Run for every approved item *before* the first effect is applied so that
        a resolution/visibility/scope failure aborts the whole decision before
        any admin-DB write commits. This is the guard against cross-DB partial
        commits: admin-DB effects (credential bind, scope grant) commit in their
        own transactions and cannot be rolled back by the control-DB transaction,
        so the only safe place to fail is up front.

        ``plan_governance`` is computed by ``decide()`` from the request's live
        fulfilment intents (see :func:`plan_governance_for_items`). A governed
        bind item can only be satisfied by the credential id the wizard stamps
        (``resource_id``); a plain approval of one is denied with an
        actionable, plan-aware reason that names the intent id(s) still
        awaiting fulfilment rather than the cryptic "no credential covers API"
        a plain approval would otherwise surface. Default (``UNGOVERNED_PLAN``)
        means "plain contract" — non-plan items and non-plan requests never
        construct a governance value at all.
        """
        key = (item.resource_type, item.action)
        if key == ("credential", "bind"):
            if plan_governance.is_governed and not item.resource_id:
                raise ProvisioningPlanNotFulfilledError(
                    item.resource_type,
                    item.action,
                    governing_intent_ids=plan_governance.governing_intent_ids,
                    governing_api=plan_governance.governing_api,
                )
            # A rules-less bind would be a live default-deny the operator
            # believes granted — the "hollow yes" as the default path (hard
            # problem 6). Filing substitutes a read-only default, so this
            # fires only for stored legacy items or amendments that stripped
            # the policy. Raise (rather than DENY) so the request stays
            # pending while the operator amends rules back on.
            if not item.rules and not item.rule_set_id:
                raise RulesRequiredForBindError()
            if item.rule_set_id is not None:
                rule_set = await PermissionRuleSetRepository.get_by_id(
                    control_session, item.rule_set_id
                )
                if rule_set is None:
                    raise RuleSetNotFoundForBindError(item.rule_set_id)
            await self._resolve_credential_bind_target(
                item, identity=identity, session=control_session
            )
        elif key == ("scope", "grant"):
            # Mirror _apply_scope_grant's guard so a bad scope-grant item fails
            # here (422) rather than mid-apply with a bare ValueError (500).
            assert_grantable_scope(item.resource_id)
        elif classify_effect(item.resource_type, item.action) is EffectPhase.FULFILMENT_ONLY:
            # Fulfilment-only intents (credential:provision) are inert
            # placeholders — the applicator never mutates state for them.
            # They still cannot carry enforceable rules (there is no binding key
            # to attach them to), so reject rules up front, consistent with the
            # file/amend-time guard. Everything else validates cleanly.
            if item.rules:
                raise RulesNotSupportedForBindError(item.resource_type, item.action)
        else:
            # Retired vocabulary (toolkit:create / toolkit:bind) or an unknown
            # pair on a stored item: hard-fail the decision (422) — never the
            # old UNSUPPORTED silent skip. See UnsupportedAccessRequestItemError.
            raise UnsupportedAccessRequestItemError(item.resource_type, item.action)

    async def _resolve_credential_bind_target(
        self, item: AccessRequestItem, *, identity: Identity, session: Any
    ) -> str:
        """Resolve (and authorize) the credential id a ``credential:bind`` targets.

        Shared by ``validate()`` (which discards the id, using this only as the
        side-effect-free visibility/resolution guard) and ``prepare()`` (which
        writes rules against — and binds to — the returned id) so the two stay
        in lock-step.

        An explicit ``resource_id`` must resolve to a credential visible to the
        decider — the visibility filters mirror :func:`build_access_filters`
        for ``Credential`` so this read sees exactly what the apply step's
        write would (issue #649). A ``resource_reference`` resolves through
        :meth:`EffectsRepository.resolve_credentials_for_api` under the
        decider's owner axis (hard problem 8): credentials the decider owns,
        plus credentials bound to agents the decider owns (the id list pushed
        down from the admin DB by :meth:`_binding_widened_credential_ids`).
        Raises ``CredentialReferenceUnresolvedError`` when no visible
        credential covers the API and ``CredentialReferenceAmbiguousError``
        when several do.
        """
        if item.resource_id:
            filters = build_access_filters(identity, Credential)
            credential = await CredentialRepository.get_by_id(
                session, item.resource_id, filters=filters
            )
            if credential is None:
                raise CredentialNotFoundForBindError(item.resource_id)
            return item.resource_id

        reference = item.resource_reference or {}
        vendor = reference.get("vendor")
        if not vendor:
            raise RequiredFieldMissingError(
                "resource_id",
                context=(
                    "credential:bind requires a credential id or a resource_reference with a vendor"
                ),
            )

        owner_ids = credential_owner_scope(identity)
        bound_ids = await self._binding_widened_credential_ids(owner_ids)
        # Normalize vendor/name to the registry's slug form (dots -> dashes) so
        # the reference matches the credential's stored, normalized api_vendor.
        # Agents file references from discovered vendor/name that may be raw
        # domains (e.g. httpbin.org); credentials store the slug (httpbin-org),
        # so an un-normalized match would find no credential and deny a
        # satisfiable bind. See issue #656.
        raw_name = reference.get("name")
        raw_version = reference.get("version")
        candidates = await EffectsRepository.resolve_credentials_for_api(
            session,
            vendor=slugify_api_field(str(vendor)),
            name=slugify_api_field(str(raw_name)) if raw_name else None,
            version=str(raw_version) if raw_version else None,
            owner_ids=owner_ids,
            bound_credential_ids=bound_ids,
        )
        if not candidates:
            raise CredentialReferenceUnresolvedError(reference)
        if len(candidates) > 1:
            raise CredentialReferenceAmbiguousError(reference, candidates)
        return candidates[0]

    async def _binding_widened_credential_ids(
        self, owner_ids: list[str] | None
    ) -> list[str] | None:
        """Admin-DB push-down for the hard-problem-8 owner axis.

        Returns the ids of credentials bound to agents owned by ``owner_ids``,
        resolved in a short admin session (the control query never references
        an admin table — hard problem 9). ``None`` for an ``org:admin`` decider
        (no restriction, so no widening needed).
        """
        if owner_ids is None:
            return None
        async with self._ctx.admin_db.session() as session:
            return await EffectsRepository.list_bound_credential_ids_for_owned_agents(
                session, owner_ids=owner_ids
            )

    async def _complete_credential_bind(
        self, item: AccessRequestItem, *, decided_by: str, prepared: PreparedEffect
    ) -> CredentialBindEffect:
        """Admin-DB half of a ``credential:bind``: the binding row + audit.

        Runs only after :meth:`prepare`'s control transaction (rules) has
        committed — see the class docstring for the ordering rationale.
        """
        assert prepared.credential_id is not None  # stamped by prepare().
        credential_id = prepared.credential_id
        rules_applied = prepared.rules_applied

        async with self._ctx.admin_db.transaction() as session:
            binding_id, already_bound = await EffectsRepository.bind_agent_to_credential(
                session,
                agent_id=item.actor_id,
                credential_id=credential_id,
                rule_set_id=item.rule_set_id,
                created_by=decided_by,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.GRANT,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=binding_id,
            actor_type=actor_type_from_id(decided_by),
            actor_id=decided_by,
            origin=None,
        )

        return CredentialBindEffect(
            binding_id=binding_id,
            credential_id=credential_id,
            rules_applied=rules_applied,
            rule_set_id=item.rule_set_id,
            already_bound=already_bound,
        )

    async def _apply_scope_grant(
        self, item: AccessRequestItem, *, decided_by: str
    ) -> ScopeGrantEffect:
        """Grant a scope to the actor.

        Only scopes in the self-service allow-list (``GRANTABLE_SCOPES``) may be
        granted this way; privileged scopes such as ``org:admin`` are rejected to
        prevent a confused-deputy escalation by an owner with ``agents:write``.
        """
        if not item.resource_id:
            raise ValueError(f"scope-grant effect requires resource_id, item={item.id}")
        scope = item.resource_id
        assert_grantable_scope(scope)
        async with self._ctx.admin_db.transaction() as session:
            created = await EffectsRepository.grant_scope_to_actor(
                session,
                actor_id=item.actor_id,
                actor_type=actor_type_from_id(item.actor_id),
                scope=scope,
                granted_by=decided_by,
                created_by=decided_by,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.GRANT,
            target_type=AuditTargetType.AGENT,
            target_id=item.actor_id,
            actor_type=actor_type_from_id(decided_by),
            actor_id=decided_by,
            origin=None,
        )

        return ScopeGrantEffect(scope=scope, already_granted=not created)
