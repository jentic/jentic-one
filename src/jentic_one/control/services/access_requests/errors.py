"""Domain exception hierarchy for the access requests service."""

from __future__ import annotations

from jentic_one.shared.access_guidance import no_credential_serves_api_reason
from jentic_one.shared.scopes import GRANTABLE_SCOPES, RETIRED_SCOPES


def _format_api_reference(reference: dict[str, object]) -> str:
    """Render a ``credential:bind`` ``resource_reference`` as a
    ``vendor[/name][@version]`` string, omitting absent parts so a vendor-only
    reference doesn't surface a misleading ``vendor/None`` in error messages."""
    vendor = reference.get("vendor")
    name = reference.get("name")
    version = reference.get("version")
    label = "/".join(str(part) for part in (vendor, name) if part)
    if version:
        label = f"{label}@{version}"
    return label or "<unspecified>"


class AccessRequestServiceError(Exception):
    """Base for all access request service errors."""


class AccessRequestNotFoundError(AccessRequestServiceError):
    """Raised when an access request identified by ID does not exist or is not visible."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"Access request '{request_id}' not found")
        self.request_id = request_id


class DuplicatePendingError(AccessRequestServiceError):
    """Raised when a pending request already exists for the same resource."""

    def __init__(self, approve_url: str, existing_request_id: str) -> None:
        super().__init__(f"A pending request already exists: '{existing_request_id}'")
        self.approve_url = approve_url
        self.existing_request_id = existing_request_id


class RequestNotPendingError(AccessRequestServiceError):
    """Raised when an operation requires pending status but the request is terminal."""

    def __init__(self, request_id: str, current_status: str) -> None:
        super().__init__(f"Access request '{request_id}' is not pending (status: {current_status})")
        self.request_id = request_id
        self.current_status = current_status


class ItemNotPendingError(AccessRequestServiceError):
    """Raised when an item-level operation targets a non-pending item."""

    def __init__(self, item_id: str, current_status: str) -> None:
        super().__init__(
            f"Access request item '{item_id}' is not pending (status: {current_status})"
        )
        self.item_id = item_id
        self.current_status = current_status


class ItemNotOnRequestError(AccessRequestServiceError):
    """Raised when a submitted item ID does not belong to the target request."""

    def __init__(self, item_id: str, request_id: str) -> None:
        super().__init__(f"Item '{item_id}' does not belong to access request '{request_id}'")
        self.item_id = item_id
        self.request_id = request_id


class NotAReviewerError(AccessRequestServiceError):
    """Raised when the caller lacks permission to review the request."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"Not authorized to review access request '{request_id}'")
        self.request_id = request_id


class AdminEffectReconcileError(AccessRequestServiceError):
    """Raised when one or more admin-DB effects could not be applied during decide().

    The decision itself is already committed and any effect that succeeded is
    acked; the listed items remain un-acked (``applied_effects IS NULL``) and are
    reconcilable by calling ``decide()`` again with the same decisions.
    """

    def __init__(self, request_id: str, item_ids: list[str]) -> None:
        super().__init__(
            f"Access request '{request_id}' decided but {len(item_ids)} admin "
            f"effect(s) failed and remain reconcilable: {', '.join(item_ids)}"
        )
        self.request_id = request_id
        self.item_ids = item_ids


class CredentialReferenceUnresolvedError(AccessRequestServiceError):
    """Raised when a credential:bind resource_reference resolves to zero credentials.

    The agent named an API (vendor/name/version) by reference, but no credential
    covering it is visible to the approver — one must be provisioned first,
    before the agent can be bound to it.
    """

    def __init__(self, reference: dict[str, object]) -> None:
        super().__init__(no_credential_serves_api_reason(_format_api_reference(reference)))
        self.reference = reference


class CredentialReferenceAmbiguousError(AccessRequestServiceError):
    """Raised when a credential:bind resource_reference resolves to several credentials.

    The approver must disambiguate by amending the item with an explicit
    resource_id (credential id).
    """

    def __init__(self, reference: dict[str, object], candidates: list[str]) -> None:
        super().__init__(
            f"Multiple credentials cover API {_format_api_reference(reference)}: "
            f"{', '.join(candidates)}; "
            "amend the item with an explicit resource_id (credential id)"
        )
        self.reference = reference
        self.candidates = candidates


class UnsupportedAccessRequestItemError(AccessRequestServiceError):
    """Raised when a stored item carries a retired (resource_type, action) pair.

    Theme-5 Phase 3 deleted the ``toolkit:create``/``toolkit:bind`` effects and
    the schema rejects them on new filings, but a stored pre-Phase-3 item (a
    pending row the auto-withdraw migration missed, or a raced filing) must
    fail **loudly** on decide — never the old ``UNSUPPORTED`` silent skip,
    which would approve-and-grant-nothing (the "hollow yes"). The directive
    names the surviving verb so the caller can re-file.
    """

    def __init__(self, resource_type: str, action: str) -> None:
        super().__init__(
            f"{resource_type}:{action} items are no longer supported. Toolkits were "
            "retired; withdraw this request and re-file with "
            "resource_type='credential', action='bind' naming the API by "
            "resource_reference (or a credential id in resource_id)."
        )
        self.resource_type = resource_type
        self.action = action


class CredentialNotFoundForBindError(AccessRequestServiceError):
    """Raised when a credential:bind item names a credential that does not exist or is not visible.

    The decider tried to bind an agent to a credential (by ``resource_id``), but
    no credential with that id is visible in the control DB — typically because
    the agent referenced a credential id that was never provisioned, or one owned
    by another operator. Surfaced as a 422 so the bad item fails up front rather
    than as a bare ``ValueError``/FK fault mid-apply (a 500). See issue #649.
    """

    def __init__(self, credential_id: str) -> None:
        super().__init__(
            f"Credential '{credential_id}' not found or not visible; "
            "provision the credential before binding an agent to it"
        )
        self.credential_id = credential_id


class UnsupportedScopeGrantError(AccessRequestServiceError):
    """Raised when a scope:grant requests a scope outside the self-service allow-list."""

    def __init__(self, scope: str) -> None:
        super().__init__(
            f"Scope '{scope}' cannot be granted via an access request; "
            "it is privileged or not in the self-service allow-list"
        )
        self.scope = scope


class RulesNotSupportedForBindError(AccessRequestServiceError):
    """Raised when permission rules accompany an item type that cannot enforce them.

    Broker rules are keyed per ``(agent_id, credential_id)`` binding (see
    ``broker/repos/agent_rule_evaluator.py``), so only a ``credential:bind`` has
    a key to enforce rules on. Attaching rules to e.g. a ``scope:grant`` would
    silently produce an unenforced allowlist — granted scope ≠ enforced scope.
    We reject it at the boundary and point the caller at ``credential:bind``.
    """

    def __init__(self, resource_type: str, action: str) -> None:
        super().__init__(
            f"Permission rules are not supported on {resource_type}:{action} items. "
            "Rules are enforced per (agent, credential) binding, so they can only "
            "be attached to credential:bind items. To set rules, file an access "
            "request with resource_type='credential', action='bind' and include "
            "your rules there."
        )
        self.resource_type = resource_type
        self.action = action


class RulesRequiredForBindError(AccessRequestServiceError):
    """Raised when a credential:bind item carries neither rules nor a rule set.

    A rules-less agent↔credential binding is a live **default-deny** the
    operator believes granted — the "hollow yes" as the default path (theme-5
    hard problem 6). Every ``credential:bind`` must therefore carry either
    inline ``rules`` or a ``rule_set_id``; ``validate()`` rejects a rules-less
    bind before any effect is applied. The filing path substitutes a read-only
    default when neither is provided, so this surfaces only for stored legacy
    items or amendments that stripped the policy.
    """

    def __init__(self) -> None:
        super().__init__(
            "credential:bind requires a policy: provide inline 'rules' or a "
            "'rule_set_id' on the item (amend it via POST "
            "/access-requests/{id}:amend). A binding without rules would be a "
            "default-deny grant."
        )


class RuleSetNotFoundForBindError(AccessRequestServiceError):
    """Raised when a credential:bind names a ``rule_set_id`` that does not resolve.

    The rule-set pointer is FK-less across the control/admin seam, so the
    application validates it at decide time: the set must exist (and remain
    visible) for the binding's policy to be real. Approving past a dangling
    pointer would create a live default-deny binding — the same hollow-yes
    shape :class:`RulesRequiredForBindError` guards against.
    """

    def __init__(self, rule_set_id: str) -> None:
        super().__init__(
            f"Permission rule set '{rule_set_id}' not found; create it first "
            "(POST /permission-rule-sets) or amend the item with inline rules"
        )
        self.rule_set_id = rule_set_id


class RequiredFieldMissingError(AccessRequestServiceError):
    """Raised when a required field is absent on an access request item."""

    def __init__(self, field: str, *, context: str) -> None:
        super().__init__(
            f"Required field '{field}' is missing on the access request item; {context}"
        )
        self.field = field
        self.context = context


class ProvisioningPlanNotFulfilledError(AccessRequestServiceError):
    """Raised when a provisioning plan's bind item is approved before fulfilment.

    A provisioning plan carries an inert ``credential:provision`` intent that a
    human fulfils in the setup wizard — which creates the real credential and
    stamps its id onto the ``credential:bind`` item. Approving the plan through
    any other path (the plain approve/deny surface, a raw ``:decide``) leaves
    the bind with no target, so it can never succeed. We deny it with an
    actionable reason instead of the cryptic "no credential covers API" a plain
    approval would otherwise produce. This error is in
    ``_UNFULFILLABLE_BIND_TARGET`` so the ``--wait`` loop closes with a legible
    message.

    ``governing_intent_ids`` — populated by the caller from the ``PlanGovernance``
    value ``decide()`` computed — names the specific fulfilment intents whose
    approval the wizard is still waiting on. ``governing_api`` additionally
    names the canonical ``(vendor, name)`` slug key that tied the plan to this
    bind. Both are ``None``/empty when the caller doesn't have the richer
    context (older call-sites, tests that construct the error directly),
    keeping the constructor backwards-compatible.
    """

    def __init__(
        self,
        resource_type: str,
        action: str,
        *,
        governing_intent_ids: frozenset[str] | None = None,
        governing_api: tuple[str, str | None] | None = None,
    ) -> None:
        base = (
            f"{resource_type}:{action} is part of a provisioning plan that has not been "
            "fulfilled yet. Approve this request from the setup wizard, which creates the "
            "credential and wires it before granting — a plain approval cannot "
            "complete a plan."
        )
        details: list[str] = []
        if governing_api is not None:
            vendor, name = governing_api
            api_label = f"{vendor}/{name}" if name else vendor
            details.append(f"governing api: {api_label}")
        if governing_intent_ids:
            details.append("awaiting intent(s): " + ", ".join(sorted(governing_intent_ids)))
        if details:
            base = f"{base} ({'; '.join(details)})"
        super().__init__(base)
        self.governing_intent_ids: frozenset[str] = frozenset(governing_intent_ids or ())
        self.governing_api: tuple[str, str | None] | None = governing_api
        self.resource_type = resource_type
        self.action = action


def assert_grantable_scope(scope: str | None) -> None:
    """Raise UnsupportedScopeGrantError unless ``scope`` is self-service grantable.

    Single source of truth for the scope:grant allow-list check, shared by the
    file-time guard (AccessRequestService) and the decide-time guard
    (EffectApplicator) so the two can never drift. A falsy scope is reported as
    a missing-field error. See issue #672.

    Scopes in :data:`~jentic_one.shared.scopes.RETIRED_SCOPES` are accepted
    without complaint: stored requests and grants written before the theme-5
    Phase 5b retirement still carry them, and a re-submit must not 422.
    Granting one is inert — no route requires a retired scope and the
    implication map no longer expands it.
    """
    if not scope:
        raise RequiredFieldMissingError("resource_id", context="scope:grant requires a scope value")
    if scope in RETIRED_SCOPES:
        return
    if scope not in GRANTABLE_SCOPES:
        raise UnsupportedScopeGrantError(scope)
