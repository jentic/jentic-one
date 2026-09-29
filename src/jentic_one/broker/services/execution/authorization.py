"""Execution authorization — the single policy both execute callers enforce.

The sync execute route and the async worker ("one pipeline, two callers") must
reach the same allow/deny verdict for the same actor, API and operation. This
module owns that verdict: derive the caller's bindings for the discovered API
(direct agent→credential bindings, or the legacy toolkit path when
``broker.direct_bindings_enabled`` is off), select the credential, and evaluate
the binding's permission rules — all **before** any secret is decrypted or
audited. A denial raises the broker domain taxonomy
(``broker/core/exceptions.py``); the web edge maps it to problem+json and the
worker records it on the job result (see :mod:`.queued_authorization`).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from jentic_one.broker.core.denial import DenialReason
from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    AmbiguousMatchError,
    BrokerError,
    CredentialIdentityMismatchError,
    action_denied_directive,
    ambiguous_toolkit_directive,
    credential_identity_mismatch_directive,
    direct_action_denied_directive,
    direct_credential_identity_mismatch_directive,
    no_credential_binding_directive,
    no_toolkit_binding_directive,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.shared.access_guidance import connect_vendor_key
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    AgentRuleEvaluatorProtocol,
    CredentialDerivation,
    CredentialDeriverProtocol,
    RuleEvaluatorProtocol,
    ToolkitDerivation,
    ToolkitDeriverProtocol,
)
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.schemas import APIReference

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
# Denial observability (theme-5): every authorization denial increments this
# counter with a closed-enum ``reason`` (DenialReason) + the API ``vendor`` and
# the authorization ``mode`` (toolkit | direct), so the cutover can be watched
# as a denial-rate delta per reason rather than grepping event summaries.
_authz_denied = _meter.create_counter(
    "broker.authorization.denied",
    description="Execute requests denied by the authorization layer, by reason",
)


def _connect_vendor_for(ctx: Context, api: APIReference) -> str | None:
    """Resolve the vendor-registry key covering ``api``, if any (Phase 1b).

    Gates the missing-binding directives' ``suggested_command`` (``jentic
    connect <vendor>``) on the registry: the connect surface takes the
    registry key, not the API identity, and suggesting a connect for an
    off-registry API would send the agent into a guaranteed
    ``unknown vendor`` error. The broker call-path owns ``AppConfig`` via
    ``ctx``, so the reverse map is a pure config scan — no I/O.
    """
    return connect_vendor_key(
        ctx.config.vendors, vendor=api.vendor, name=api.name, version=api.version
    )


def _empty_derivation_denial(
    d: ToolkitDerivation,
    api: APIReference,
    *,
    instance: str,
    connect_vendor: str | None = None,
) -> BrokerError:
    """Pick the right denial for an empty toolkit derivation (#683 + #747/#748).

    Two cases, distinguished by the structured derivation result — each with its
    own ``detail`` so the problem+json ``type`` and ``detail`` never tell
    different stories (e.g. a ``credential_identity_mismatch`` must not carry a
    "not bound to toolkit" detail):

    - Bound + a bound credential is a near-miss for the API → the credential's
      identity does not cover the operation (#747/#748). Fix the *credential* —
      an operator action; no new binding would help.
    - Otherwise → ``no_toolkit_binding``, whose recovery ask (bind to the
      serving credential vs. provision one first) is chosen by
      ``no_toolkit_binding_directive``
      from whether any toolkit serves the API at all (#683).
    """
    serves = bool(d.api_served_toolkits)
    if d.agent_bound_any and not serves and d.identity_mismatch is not None:
        return CredentialIdentityMismatchError(
            "A bound credential's identity does not cover this API",
            type="credential_identity_mismatch",
            instance=instance,
            directive=credential_identity_mismatch_directive(mismatch=d.identity_mismatch),
        )
    return ActionDeniedError(
        "No toolkit binding for this API",
        type="no_toolkit_binding",
        instance=instance,
        directive=no_toolkit_binding_directive(
            vendor=api.vendor,
            name=api.name,
            version=api.version,
            toolkit_serves_api=serves,
            connect_vendor=connect_vendor,
        ),
    )


def _is_unserved_no_toolkit_binding(exc: ActionDeniedError) -> bool:
    """True when ``exc`` denies with the pre-binding "no toolkit serves this API" case.

    Splits the two ``no_toolkit_binding`` flavours ``_empty_derivation_denial``
    emits: ``serves=True`` (a toolkit exists, the caller just isn't bound) is
    a routine bind the operator grants on the agent's ask and does not warrant
    an operator
    event; ``serves=False`` (nothing serves this API yet — a credential must be
    provisioned first) is the operator-attention case, mirroring the 424
    ``CREDENTIAL_NOT_PROVISIONED`` event on the post-binding side.
    """
    if exc.type != "no_toolkit_binding" or exc.directive is None:
        return False
    return exc.directive.parameters.get("toolkit_serves_api") is False


async def _emit_toolkit_binding_unserved(
    ctx: Context, *, api: APIReference, identity: Identity
) -> None:
    """Emit ``TOOLKIT_BINDING_UNSERVED`` best-effort (mirrors the PBAC_DENIED emit).

    Fires once per denied execute request (the caller wraps `select_toolkit`);
    the caller's own retry loop will re-emit — acceptable at WARNING severity,
    and consistent with how ``CREDENTIAL_NOT_PROVISIONED`` (424) already emits
    per attempt without in-process debouncing.
    """
    api_id = "/".join(part for part in (api.vendor, api.name) if part) or api.vendor
    summary = f"No toolkit serves API '{api_id}' — provision a credential to enable binding."
    try:
        async with ctx.admin_db.transaction() as session:
            await emit_event_best_effort(
                session,
                type=EventType.TOOLKIT_BINDING_UNSERVED,
                severity=EventSeverity.WARNING,
                summary=summary,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "api": {
                        "vendor": api.vendor,
                        "name": api.name,
                        "version": api.version,
                    },
                },
            )
    except Exception:
        logger.warning(
            "telemetry_emit_failed",
            event_type=EventType.TOOLKIT_BINDING_UNSERVED,
            exc_info=True,
        )


@dataclass(frozen=True, slots=True)
class ToolkitSelection:
    """The toolkit an execution runs against, and its injection boundary.

    ``credential_ids`` are the selected toolkit's bound credentials that cover
    the API — the only credentials the toolkit path may inject. Empty means
    nothing may resolve (fail closed), never "no filter".
    """

    toolkit_id: str
    credential_ids: tuple[str, ...]


async def select_toolkit(
    *,
    deriver: ToolkitDeriverProtocol,
    identity: Identity,
    api: APIReference,
    header_toolkit: str | None,
    instance: str,
    connect_vendor: str | None = None,
) -> ToolkitSelection:
    """Derive the toolkit for this execution from the caller's bindings.

    ``0 → 403`` (no binding / credential identity mismatch), ``1 → use it``,
    ``N → 409`` (caller must disambiguate with ``Jentic-Toolkit-Id``). A supplied
    header is validated against the derived candidates; never silently honoured or
    silently picked.

    Non-agent actors (users, and not-yet-migrated service accounts) follow
    the **same** derivation rule — there is no implicit bypass. Toolkit keys
    — the one actor kind that authenticated *as* a toolkit and skipped
    derivation — are retired (theme-5 Phase 4): a presented ``jntc_live_``
    plaintext resolves as the successor the retirement job bound to the same
    toolkit, so it derives here like any other caller.
    """
    # Invariant: the API identity here is the *discovered* spec identity, which is
    # always concrete (vendor/name/version all set) — the registry never yields a
    # wildcard. Derivation and the nearest-miss diagnostic (#748) rely on this
    # (they slugify and compare each axis), so assert it at the boundary rather
    # than silently deriving against a blank axis.
    assert api.vendor and api.name and api.version, (
        "select_toolkit requires a concrete discovered API identity"
    )

    derivation = await deriver.derive_toolkits(
        agent_id=identity.sub,
        vendor=api.vendor,
        name=api.name,
        version=api.version,
    )
    candidates = list(derivation.toolkits)

    if header_toolkit:
        if header_toolkit not in candidates:
            # Recoverable: the agent named a toolkit it isn't bound to. Carry the
            # agent-recovery contract like every other broker denial — point it at
            # the toolkits it *is* bound to (switch_toolkit) or, if it has none,
            # at the correct provisioning/binding/credential-fix step. A bare
            # Forbidden here would be a dead-end 403 with no directive.
            if candidates:
                raise ActionDeniedError(
                    f"Not bound to toolkit '{header_toolkit}' for this API",
                    type="toolkit_binding_required",
                    instance=instance,
                    directive=ambiguous_toolkit_directive(candidates),
                )
            raise _empty_derivation_denial(
                derivation, api, instance=instance, connect_vendor=connect_vendor
            )
        return _selection(derivation, header_toolkit)

    if not candidates:
        raise _empty_derivation_denial(
            derivation, api, instance=instance, connect_vendor=connect_vendor
        )
    if len(candidates) > 1:
        raise AmbiguousMatchError(
            "Multiple toolkits match this API; resend with the Jentic-Toolkit-Id header.",
            type="ambiguous_toolkit",
            instance=instance,
            extra={
                "errors": [
                    {
                        "detail": f"Candidate toolkit '{tk}'.",
                        "header": "Jentic-Toolkit-Id",
                        "code": tk,
                    }
                    for tk in candidates
                ]
            },
            directive=ambiguous_toolkit_directive(candidates),
        )
    return _selection(derivation, candidates[0])


def _selection(derivation: ToolkitDerivation, toolkit_id: str) -> ToolkitSelection:
    return ToolkitSelection(
        toolkit_id=toolkit_id,
        credential_ids=tuple(derivation.credentials_by_toolkit.get(toolkit_id, ())),
    )


def _empty_credential_derivation_denial(
    d: CredentialDerivation,
    api: APIReference,
    *,
    instance: str,
    connect_vendor: str | None = None,
) -> BrokerError:
    """Pick the right denial for an empty credential derivation (direct path).

    The direct-binding twin of :func:`_empty_derivation_denial` — two cases,
    each with its own ``detail`` so the problem+json ``type`` and ``detail``
    never tell different stories:

    - Bound + a bound credential is a near-miss for the API → the credential's
      identity does not cover the operation (#747/#748 twin). Fix the
      *credential*, never request another binding.
    - Otherwise → ``no_credential_binding``, whose recovery (grant a binding
      vs. provision a credential first) is chosen by
      :func:`no_credential_binding_directive` from whether any credential
      serves the API at all.
    """
    if d.agent_bound_any and not d.api_served and d.identity_mismatch is not None:
        _authz_denied.add(
            1,
            {
                "reason": DenialReason.CREDENTIAL_IDENTITY_MISMATCH.value,
                "mode": "direct",
                "vendor": api.vendor,
            },
        )
        return CredentialIdentityMismatchError(
            "A bound credential's identity does not cover this API",
            type="credential_identity_mismatch",
            instance=instance,
            directive=direct_credential_identity_mismatch_directive(mismatch=d.identity_mismatch),
        )
    _authz_denied.add(
        1,
        {
            "reason": DenialReason.NO_CREDENTIAL_BINDING.value,
            "mode": "direct",
            "vendor": api.vendor,
        },
    )
    return ActionDeniedError(
        "No credential binding for this API",
        type="no_credential_binding",
        instance=instance,
        directive=no_credential_binding_directive(
            vendor=api.vendor,
            name=api.name,
            version=api.version,
            api_served=d.api_served,
            connect_vendor=connect_vendor,
        ),
    )


async def _emit_credential_binding_unserved(
    ctx: Context, *, api: APIReference, identity: Identity
) -> None:
    """Emit ``CREDENTIAL_BINDING_UNSERVED`` best-effort (direct-path twin).

    Mirrors ``_emit_toolkit_binding_unserved``: fires once per denied execute
    request when nothing serves the API at all — the operator-attention case
    (a credential must be provisioned before any binding can be granted).
    """
    api_id = "/".join(part for part in (api.vendor, api.name) if part) or api.vendor
    summary = f"No credential serves API '{api_id}' — provision one to enable binding."
    try:
        async with ctx.admin_db.transaction() as session:
            await emit_event_best_effort(
                session,
                type=EventType.CREDENTIAL_BINDING_UNSERVED,
                severity=EventSeverity.WARNING,
                summary=summary,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "api": {
                        "vendor": api.vendor,
                        "name": api.name,
                        "version": api.version,
                    },
                },
            )
    except Exception:
        logger.warning(
            "telemetry_emit_failed",
            event_type=EventType.CREDENTIAL_BINDING_UNSERVED,
            exc_info=True,
        )


async def derive_credential_bindings(
    *,
    deriver: CredentialDeriverProtocol,
    identity: Identity,
    api: APIReference,
    instance: str,
    ctx: Context,
) -> CredentialDerivation:
    """Derive the caller's credential-binding candidates for this execution.

    The direct-binding half of what :func:`select_toolkit` does for toolkits:
    ``0 → 403`` (no binding / credential identity mismatch — with the
    operator-visible ``CREDENTIAL_BINDING_UNSERVED`` emit for the pre-binding
    nothing-serves case). Candidate *selection* among ``N ≥ 1`` (name header →
    most-specific-wins → ``Jentic-Credential-Id`` tie-breaker → 409) is owned
    by ``CredentialService.select``, which shares the resolver with injection
    so selection and injection can never disagree.

    Non-agent actors (users) follow the **same** derivation rule — no
    implicit bypass, mirroring the toolkit path. Toolkit keys never
    reach here (the caller keeps them on the legacy path until Phase 4).
    """
    assert api.vendor and api.name and api.version, (
        "derive_credential_bindings requires a concrete discovered API identity"
    )
    derivation = await deriver.derive_credentials(
        agent_id=identity.sub,
        vendor=api.vendor,
        name=api.name,
        version=api.version,
    )
    if not derivation.credentials:
        denial = _empty_credential_derivation_denial(
            derivation,
            api,
            instance=instance,
            connect_vendor=_connect_vendor_for(ctx, api),
        )
        # The operator-visible pre-binding signal fires only for the plain
        # no-binding + nothing-serves case — an identity mismatch already has
        # its own actionable diagnostic (mirrors the toolkit path's
        # ``_is_unserved_no_toolkit_binding`` gate).
        if not derivation.api_served and isinstance(denial, ActionDeniedError):
            await _emit_credential_binding_unserved(ctx, api=api, identity=identity)
        raise denial
    return derivation


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    """An allowed execution's authorization outcome.

    ``allowed_credential_ids`` is the injection boundary (the caller's bound
    credentials on the direct path, the selected toolkit's on the toolkit
    path) — only these ids may resolve at injection, and an empty list
    resolves nothing. ``selected_credential`` is the direct path's
    already-selected credential (``None`` on the toolkit path, which resolves
    at injection); ``toolkit_id`` is the toolkit path's selected toolkit.
    """

    toolkit_id: str | None
    allowed_credential_ids: list[str]
    selected_credential: ResolvedCredential | None


async def authorize_execution(
    *,
    ctx: Context,
    identity: Identity,
    api: APIReference,
    operation_id: str | None,
    method: str,
    path: str,
    instance: str,
    deriver: ToolkitDeriverProtocol,
    rule_evaluator: RuleEvaluatorProtocol,
    credential_deriver: CredentialDeriverProtocol,
    agent_rule_evaluator: AgentRuleEvaluatorProtocol,
    credential_name: str | None = None,
    credential_id: str | None = None,
    toolkit_id: str | None = None,
) -> ExecutionAuthorization:
    """Authorize one execution for ``identity`` against the discovered ``api``.

    ``credential_name`` / ``credential_id`` / ``toolkit_id`` are the caller's
    disambiguation inputs (``Jentic-Credential-Name`` / ``Jentic-Credential-Id``
    / ``Jentic-Toolkit-Id`` on the sync path; the enqueue-time selection on the
    worker). ``path`` is the upstream URL path the rules match against and
    ``instance`` the RFC 9457 ``instance`` a denial carries.

    Raises a :class:`BrokerError` (``ActionDeniedError`` /
    ``CredentialIdentityMismatchError`` → 403, ``AmbiguousMatchError`` → 409,
    and the credential-resolution taxonomy from ``CredentialService.select``)
    when the execution is not allowed.
    """
    selected_credential: ResolvedCredential | None = None
    selected_toolkit_id: str | None = None
    allowed_credential_ids: list[str]

    if ctx.config.broker.direct_bindings_enabled:
        # Derive candidates (0 → 403 with the right directive), then select the
        # single credential (name header → most-specific-wins →
        # Jentic-Credential-Id tie-breaker; genuine tie → 409), then enforce the
        # binding's rules — all before any secret is decrypted or audited.
        derivation = await derive_credential_bindings(
            deriver=credential_deriver,
            identity=identity,
            api=api,
            instance=instance,
            ctx=ctx,
        )
        allowed_credential_ids = [bc.credential_id for bc in derivation.credentials]
        rule_set_ids = {bc.credential_id: bc.rule_set_id for bc in derivation.credentials}
        selected_credential = await CredentialService(ctx).select(
            api_vendor=api.vendor,
            api_name=api.name,
            api_version=api.version,
            identity=identity,
            credential_name=credential_name,
            credential_id=credential_id,
            allowed_credential_ids=allowed_credential_ids,
        )
        assert selected_credential is not None  # api.vendor is concrete (asserted above)
        evaluation = await agent_rule_evaluator.evaluate(
            agent_id=identity.sub,
            credential_id=selected_credential.credential_id,
            rule_set_id=rule_set_ids.get(selected_credential.credential_id),
            method=method,
            path=path,
            operation_id=operation_id,
        )
        if not evaluation.allowed:
            # Same two-variant deny split as the toolkit path (#578): an empty
            # rule list (nothing configured for this binding) vs loaded rules
            # where none allowed the request.
            no_rules = evaluation.rules_loaded == 0
            reason = DenialReason.NO_RULES_LOADED if no_rules else DenialReason.NO_RULE_MATCHED
            _authz_denied.add(
                1,
                {"reason": reason.value, "mode": "direct", "vendor": api.vendor},
            )
            summary = (
                "Operation denied by credential-binding permission rules (no rules "
                "configured for this binding)"
                if no_rules
                else "Operation denied by credential-binding permission rules (no rule matched)"
            )
            detail = (
                "The requested operation is denied — this credential binding has no "
                "permission rules configured. Attach rules under "
                "PUT /credentials/{credential_id}/agents/{agent_id}/permissions "
                "or attach a rule set."
                if no_rules
                else "The requested operation is denied by a credential-binding permission rule."
            )
            try:
                async with ctx.admin_db.transaction() as session:
                    await emit_event_best_effort(
                        session,
                        type=EventType.PBAC_DENIED,
                        severity=EventSeverity.WARNING,
                        summary=summary,
                        created_by=identity.sub,
                        actor_id=identity.sub,
                        actor_type=identity.actor_type.value,
                        data={"reason": reason.value, "mode": "direct"},
                    )
            except Exception:
                logger.warning(
                    "telemetry_emit_failed", event_type=EventType.PBAC_DENIED, exc_info=True
                )
            raise ActionDeniedError(
                detail=detail,
                type="action_denied",
                instance=instance,
                directive=direct_action_denied_directive(),
            )
    else:
        # Toolkit is derived from the discovered API identity (never the inbound
        # header verbatim); drives credential injection and execution attribution.
        try:
            selection = await select_toolkit(
                deriver=deriver,
                identity=identity,
                api=api,
                header_toolkit=toolkit_id,
                instance=instance,
                connect_vendor=_connect_vendor_for(ctx, api),
            )
        except ActionDeniedError as exc:
            # Emit the operator-visible signal for the pre-binding no-toolkit case
            # (nothing serves this API yet) before re-raising. The 424
            # ``credential_not_provisioned`` path already emits
            # ``CREDENTIAL_NOT_PROVISIONED`` (post-binding); this is the missing
            # pre-binding twin. See ``TOOLKIT_BINDING_UNSERVED``.
            if _is_unserved_no_toolkit_binding(exc):
                await _emit_toolkit_binding_unserved(ctx, api=api, identity=identity)
            raise
        selected_toolkit_id = selection.toolkit_id
        # Injection boundary for the toolkit path: only the selected toolkit's
        # bound credentials may resolve. Without it the resolver would consider
        # every credential in the tenant for this vendor — including another
        # user's, reachable by name via Jentic-Credential-Name.
        allowed_credential_ids = list(selection.credential_ids)

        # Evaluate toolkit permission rules — default-deny when no rule matches.
        # Unconditional: even if toolkit_id were empty the evaluator returns a
        # zero-rules-loaded denial, preserving the secure-by-default posture.
        evaluation = await rule_evaluator.evaluate(
            toolkit_id=selected_toolkit_id,
            method=method,
            path=path,
            operation_id=operation_id,
            api_vendor=api.vendor,
        )
        if not evaluation.allowed:
            # #578: distinguish the two deny paths in the caller-visible detail.
            # ``rules_loaded == 0`` means the vendor-pooled rule set is empty —
            # nothing to match (wrong vendor, empty binding, misconfigured store);
            # otherwise we loaded rules but none matched the request shape. Both
            # branches emit ``PBAC_DENIED`` telemetry with the corresponding
            # summary so operators can grep for the branch.
            no_rules = evaluation.rules_loaded == 0
            reason = DenialReason.NO_RULES_LOADED if no_rules else DenialReason.NO_RULE_MATCHED
            _authz_denied.add(
                1,
                {"reason": reason.value, "mode": "toolkit", "vendor": api.vendor},
            )
            summary = (
                "Operation denied by toolkit permission rule (no rules loaded for this vendor)"
                if no_rules
                else "Operation denied by toolkit permission rule (no rule matched)"
            )
            detail = (
                "The requested operation is denied — no permission rules are loaded for the "
                "target API's vendor on this binding. Ask your operator to attach rules "
                "(with direct bindings, under "
                "PUT /credentials/{credential_id}/agents/{agent_id}/permissions)."
                if no_rules
                else "The requested operation is denied by a toolkit permission rule."
            )
            try:
                async with ctx.admin_db.transaction() as session:
                    await emit_event_best_effort(
                        session,
                        type=EventType.PBAC_DENIED,
                        severity=EventSeverity.WARNING,
                        summary=summary,
                        created_by=identity.sub,
                        actor_id=identity.sub,
                        actor_type=identity.actor_type.value,
                        data={"reason": reason.value, "mode": "toolkit"},
                    )
            except Exception:
                logger.warning(
                    "telemetry_emit_failed", event_type=EventType.PBAC_DENIED, exc_info=True
                )
            raise ActionDeniedError(
                detail=detail,
                type="action_denied",
                instance=instance,
                directive=action_denied_directive(),
            )

    return ExecutionAuthorization(
        toolkit_id=selected_toolkit_id,
        allowed_credential_ids=allowed_credential_ids,
        selected_credential=selected_credential,
    )
