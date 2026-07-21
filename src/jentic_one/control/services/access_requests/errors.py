"""Domain exception hierarchy for the access requests service."""

from __future__ import annotations

from jentic_one.shared.scopes import GRANTABLE_SCOPES


def _format_api_reference(reference: dict[str, object]) -> str:
    """Render a toolkit:bind ``resource_reference`` as a ``vendor[/name][@version]``
    string, omitting absent parts so a vendor-only reference doesn't surface a
    misleading ``vendor/None`` in error messages."""
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


class PrerequisiteNotMetError(AccessRequestServiceError):
    """Raised when a prerequisite binding is missing for the requested resource."""

    def __init__(self, actor_id: str, to_id: str, resource_type: str) -> None:
        super().__init__(
            f"Prerequisite not met: {resource_type} binding between "
            f"actor '{actor_id}' and target '{to_id}' does not exist"
        )
        self.actor_id = actor_id
        self.to_id = to_id
        self.resource_type = resource_type


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


class ToolkitReferenceUnresolvedError(AccessRequestServiceError):
    """Raised when a toolkit:bind resource_reference resolves to zero toolkits.

    The agent named an API (vendor/name/version) by reference, but no toolkit
    serves it yet — a credential for that API must be provisioned and bound to a
    toolkit first, before an agent can be bound to it.
    """

    def __init__(self, reference: dict[str, object]) -> None:
        super().__init__(
            f"No toolkit serves API {_format_api_reference(reference)}; "
            "provision and bind a credential for it first"
        )
        self.reference = reference


class ToolkitReferenceAmbiguousError(AccessRequestServiceError):
    """Raised when a toolkit:bind resource_reference resolves to several toolkits.

    The approver must disambiguate by re-filing/amending the item with an explicit
    resource_id (toolkit id).
    """

    def __init__(self, reference: dict[str, object], candidates: list[str]) -> None:
        super().__init__(
            f"Multiple toolkits serve API {_format_api_reference(reference)}: "
            f"{', '.join(candidates)}; "
            "amend the item with an explicit resource_id (toolkit id)"
        )
        self.reference = reference
        self.candidates = candidates


class ToolkitNotVisibleError(AccessRequestServiceError):
    """Raised when a toolkit:bind targets a toolkit the decider cannot see.

    The decider tried to bind an agent to a toolkit (by explicit id) that does
    not exist or is owned by another operator/tenant. Reference-based binds that
    resolve to no *visible* toolkit surface as ``ToolkitReferenceUnresolvedError``
    instead, to avoid revealing whether the id exists elsewhere.
    """

    def __init__(self, toolkit_id: str) -> None:
        super().__init__(f"Toolkit '{toolkit_id}' not found or not owned by the approver")
        self.toolkit_id = toolkit_id


class CredentialNotFoundForBindError(AccessRequestServiceError):
    """Raised when a credential:bind item names a credential that does not exist or is not visible.

    The decider tried to bind a credential (by ``resource_id``) to a toolkit, but
    no credential with that id is visible in the control DB — typically because
    the agent referenced a credential id that was never provisioned, or one owned
    by another operator. Surfaced as a 422 so the bad item fails up front rather
    than as a bare ``ValueError``/FK fault mid-apply (a 500). See issue #649.
    """

    def __init__(self, credential_id: str) -> None:
        super().__init__(
            f"Credential '{credential_id}' not found or not visible; "
            "provision the credential before binding it to a toolkit"
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

    Broker rules are keyed per ``(toolkit_id, credential_id)`` (see
    ``broker/repos/rule_evaluator.py``), so only a ``credential:bind`` has a key
    to enforce rules on. Attaching rules to e.g. a ``toolkit:bind`` (agent↔toolkit)
    would silently produce an unrestricted binding — granted scope ≠ enforced scope.
    We reject it at the boundary and point the caller at ``credential:bind``.
    """

    def __init__(self, resource_type: str, action: str) -> None:
        super().__init__(
            f"Permission rules are not supported on {resource_type}:{action} items. "
            "Rules are enforced per (toolkit_id, credential_id) binding, so they "
            "can only be attached to credential:bind items. To set rules, file an "
            "access request with resource_type='credential', action='bind' and "
            "include your rules there (toolkits:write scope is not needed)."
        )
        self.resource_type = resource_type
        self.action = action


class RequiredFieldMissingError(AccessRequestServiceError):
    """Raised when a required field is absent on an access request item."""

    def __init__(self, field: str, *, context: str) -> None:
        super().__init__(
            f"Required field '{field}' is missing on the access request item; {context}"
        )
        self.field = field
        self.context = context


def assert_grantable_scope(scope: str | None) -> None:
    """Raise UnsupportedScopeGrantError unless ``scope`` is self-service grantable.

    Single source of truth for the scope:grant allow-list check, shared by the
    file-time guard (AccessRequestService) and the decide-time guard
    (EffectApplicator) so the two can never drift. A falsy scope is reported as
    a missing-field error. See issue #672.
    """
    if not scope:
        raise RequiredFieldMissingError("resource_id", context="scope:grant requires a scope value")
    if scope not in GRANTABLE_SCOPES:
        raise UnsupportedScopeGrantError(scope)
