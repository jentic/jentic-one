"""Execution authorization — the single policy both execute callers enforce.

The sync execute route and the async worker ("one pipeline, two callers") must
reach the same allow/deny verdict for the same actor, API and operation. This
module owns that verdict: derive the caller's bindings for the discovered API
(direct agent→credential bindings — the legacy toolkit path was deleted in
theme-5 Phase 6b), select the credential, and evaluate
the binding's permission rules — all **before** any secret is decrypted or
audited. A denial raises the broker domain taxonomy
(``broker/core/exceptions.py``); the web edge maps it to problem+json and the
worker records it on the job result (see :mod:`.queued_authorization`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from jentic_one.broker.core.denial import DenialReason
from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    BrokerError,
    CredentialIdentityMismatchError,
    direct_action_denied_directive,
    direct_credential_identity_mismatch_directive,
    no_credential_binding_directive,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.shared.access_guidance import connect_vendor_key
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    AgentRuleEvaluatorProtocol,
    CredentialDerivation,
    CredentialDeriverProtocol,
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
# the authorization ``mode`` (always ``direct`` since Phase 6b removed the
# toolkit path; the label survives so dashboards keep their shape), so denials
# can be watched as a rate delta per reason rather than grepping summaries.
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


def _empty_credential_derivation_denial(
    d: CredentialDerivation,
    api: APIReference,
    *,
    instance: str,
    connect_vendor: str | None = None,
) -> BrokerError:
    """Pick the right denial for an empty credential derivation (direct path).

    Two cases, each with its own ``detail`` so the problem+json ``type`` and ``detail``
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

    Fires once per denied execute request when nothing serves the API at all —
    the operator-attention case (a credential must be provisioned before any
    binding can be granted). The caller's own retry loop will re-emit —
    acceptable at WARNING severity, and consistent with how
    ``CREDENTIAL_NOT_PROVISIONED`` (424) emits per attempt.
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

    ``0 → 403`` (no binding / credential identity mismatch — with the
    operator-visible ``CREDENTIAL_BINDING_UNSERVED`` emit for the pre-binding
    nothing-serves case). Candidate *selection* among ``N ≥ 1`` (name header →
    most-specific-wins → ``Jentic-Credential-Id`` tie-breaker → 409) is owned
    by ``CredentialService.select``, which shares the resolver with injection
    so selection and injection can never disagree.

    Non-agent actors (users) follow the **same** derivation rule — no
    implicit bypass. Retired toolkit keys resolve as their successor agents
    and derive here like any other caller.
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
        # its own actionable diagnostic.
        if not derivation.api_served and isinstance(denial, ActionDeniedError):
            await _emit_credential_binding_unserved(ctx, api=api, identity=identity)
        raise denial
    return derivation


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    """An allowed execution's authorization outcome.

    ``allowed_credential_ids`` is the injection boundary (the caller's bound
    credentials for the API) — only these ids may resolve at injection, and an
    empty list resolves nothing. ``selected_credential`` is the credential
    already selected among them.
    """

    allowed_credential_ids: list[str]
    selected_credential: ResolvedCredential


async def authorize_execution(
    *,
    ctx: Context,
    identity: Identity,
    api: APIReference,
    operation_id: str | None,
    method: str,
    path: str,
    instance: str,
    credential_deriver: CredentialDeriverProtocol,
    agent_rule_evaluator: AgentRuleEvaluatorProtocol,
    credential_name: str | None = None,
    credential_id: str | None = None,
    request_server_variables: Mapping[str, str] | None = None,
    server_variables_unresolved: bool = False,
) -> ExecutionAuthorization:
    """Authorize one execution for ``identity`` against the discovered ``api``.

    ``credential_name`` / ``credential_id`` are the caller's disambiguation
    inputs (``Jentic-Credential-Name`` / ``Jentic-Credential-Id`` on the sync
    path; the enqueue-time selection on the worker). ``request_server_variables``
    are the request URL's concrete server-variable values; credential selection
    skips credentials scoped to other values. ``path`` is the upstream URL path
    the rules match against and ``instance`` the RFC 9457 ``instance`` a denial
    carries.

    Raises a :class:`BrokerError` (``ActionDeniedError`` /
    ``CredentialIdentityMismatchError`` → 403, ``AmbiguousMatchError`` → 409,
    and the credential-resolution taxonomy from ``CredentialService.select``)
    when the execution is not allowed.
    """
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
        request_server_variables=request_server_variables,
        server_variables_unresolved=server_variables_unresolved,
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
        # Two-variant deny split (#578): an empty rule list (nothing
        # configured for this binding) vs loaded rules where none allowed the
        # request.
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
            logger.warning("telemetry_emit_failed", event_type=EventType.PBAC_DENIED, exc_info=True)
        raise ActionDeniedError(
            detail=detail,
            type="action_denied",
            instance=instance,
            directive=direct_action_denied_directive(),
        )

    return ExecutionAuthorization(
        allowed_credential_ids=allowed_credential_ids,
        selected_credential=selected_credential,
    )
