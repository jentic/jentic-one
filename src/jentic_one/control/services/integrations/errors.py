"""Errors surfaced by the ConnectSessionService.

Mapped to problem-details HTTP responses in `control/web/routers/integrations.py`.
"""

from __future__ import annotations


class ConnectSessionServiceError(Exception):
    """Base class for connect-session service errors."""


class PointsAtAccessError(ConnectSessionServiceError):
    """An edit or amend refusal that means the caller's picture of its access is wrong.

    The response carries a ``check_access`` agent directive pointing at the
    binding's current access; :meth:`point_at` names the binding.
    """

    credential_id: str | None = None
    agent_id: str | None = None

    def point_at(self, credential_id: str | None, agent_id: str | None) -> None:
        self.credential_id = credential_id
        self.agent_id = agent_id


class SessionNotFoundError(ConnectSessionServiceError):
    pass


class InvalidStateTransitionError(ConnectSessionServiceError):
    """The session is not in a state that permits the requested operation."""

    def __init__(self, session_id: str, current_state: str, action: str) -> None:
        super().__init__(f"session {session_id!r} is in state {current_state!r}, cannot {action}")
        self.session_id = session_id
        self.current_state = current_state
        self.action = action


class InvalidPollTokenError(ConnectSessionServiceError):
    """The poll_token given does not match the session."""


class ScopeValidationError(ConnectSessionServiceError):
    """One or more confirmed scopes are not offered by the vendor."""

    def __init__(self, unknown: list[str]) -> None:
        super().__init__(f"unknown scopes for vendor: {unknown!r}")
        self.unknown = unknown


class ConfirmationForbiddenError(ConnectSessionServiceError):
    """Caller is not permitted to confirm this session (e.g. agent confirming own session)."""


class AgentRequiredError(ConnectSessionServiceError):
    """Caller is a USER/SA but agent_id was not supplied."""


class AgentNotFoundError(ConnectSessionServiceError):
    """The ``agent_id`` to bind does not exist in the admin DB."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent {agent_id!r} not found")
        self.agent_id = agent_id


class AgentInactiveError(ConnectSessionServiceError):
    """The agent to bind exists but is archived, disabled or rejected."""

    def __init__(self, agent_id: str, status: str) -> None:
        super().__init__(f"agent {agent_id!r} is {status} and cannot be bound to a credential")
        self.agent_id = agent_id
        self.status = status


class NoOpForFlowError(ConnectSessionServiceError):
    """The resolved flow is not yet implemented in phase 1."""

    def __init__(self, flow: str) -> None:
        super().__init__(f"flow {flow!r} not supported in phase 1")
        self.flow = flow


class InvalidOAuthAppRegistrationError(ConnectSessionServiceError):
    """The pinned ``oauth_app_registration_id`` on ``:connect`` is not usable.

    Raised when the caller supplied a registration id that either does not
    exist, is inactive, or references a different ``api_vendor`` than
    ``body.vendor`` — a mismatch that would otherwise silently mint
    credentials against the wrong vendor's OAuth app.

    The message is deliberately the same for every cause, so the response
    can't be used to probe which registration ids exist or which vendor they
    belong to; ``reason`` carries the specific cause for logs.
    """

    def __init__(self, registration_id: str, reason: str) -> None:
        super().__init__(
            f"oauth_app_registration {registration_id!r} is not usable for this vendor"
        )
        self.registration_id = registration_id
        self.reason = reason


class OAuthAppChangedError(ConnectSessionServiceError):
    """The OAuth app resolved at confirm differs from the one used at ``:connect``.

    The session is cancelled; the caller must start a new one so the
    credential, its aux rows, and the vendor conversation all agree on a
    single OAuth app.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"session {session_id!r} was cancelled: its OAuth app changed since "
            "it was created; start a new session"
        )
        self.session_id = session_id


class CredentialMissingCreatorError(ConnectSessionServiceError):
    """A credential with no ``created_by`` cannot finalise a connect flow.

    Every credential is created via ``POST /credentials`` behind
    ``credentials:write``, so ``created_by`` is always populated in
    practice. This is a defensive typed error so an unexpected null
    surfaces to the router's ``ConnectSessionServiceError`` handler
    with a structured ``error_code`` instead of bubbling out as a bare
    ``RuntimeError`` → 500.
    """

    def __init__(self, credential_id: str) -> None:
        super().__init__(
            f"credential {credential_id!r} has no created_by — "
            "cannot finalise a connect flow without an initiator identity"
        )
        self.credential_id = credential_id


class ManualFlowsDisabledError(ConnectSessionServiceError):
    """``:connect`` named an API target while ``control.connect.manual_flows_enabled`` is off."""

    def __init__(self) -> None:
        super().__init__("connect sessions for API targets are not enabled on this instance")


class UnsupportedTargetKindError(ConnectSessionServiceError):
    """A code path that only handles vendor-registry targets met another target kind.

    Internal: API targets are only created once their flows exist, so reaching
    this means a session row this release cannot act on.
    """

    def __init__(self, session_id: str | None, target_kind: str, action: str) -> None:
        subject = f"session {session_id!r}" if session_id else "connect request"
        super().__init__(f"{subject} has target kind {target_kind!r}; cannot {action}")
        self.session_id = session_id
        self.target_kind = target_kind
        self.action = action


class SecuritySchemesLookupUnavailableError(ConnectSessionServiceError):
    """This process cannot read the registry, so it cannot open a session for a registry API."""

    def __init__(self) -> None:
        super().__init__("connect sessions for registry APIs need registry access on this instance")


class UnknownApiError(ConnectSessionServiceError):
    """The API named by a connect request does not exist or has no live revision."""

    def __init__(self, vendor: str, name: str, version: str) -> None:
        super().__init__(f"API '{vendor}/{name}/{version}' not found or has no live revision")
        self.vendor = vendor
        self.name = name
        self.version = version


class AuthTypeRequiredError(ConnectSessionServiceError):
    """The API declares several usable schemes and the request did not pick one."""

    def __init__(self, options: list[str]) -> None:
        super().__init__(
            f"the API declares several auth schemes; pass auth_type as one of {options!r}"
        )
        self.options = options


class AuthTypeNotDeclaredError(ConnectSessionServiceError):
    """The proposed ``auth_type`` is not a scheme the API's spec declares."""

    def __init__(self, auth_type: str, options: list[str]) -> None:
        super().__init__(f"auth_type {auth_type!r} is not declared by the API's spec")
        self.auth_type = auth_type
        self.options = options


class NoDeclaredSchemeError(ConnectSessionServiceError):
    """The API's spec declares no security scheme a connect session can serve."""

    def __init__(self, vendor: str, name: str, version: str) -> None:
        super().__init__(
            f"API '{vendor}/{name}/{version}' declares no supported security scheme; "
            "its spec must declare its auth, or an operator creates the credential directly"
        )


class ReservedAuthFieldError(ConnectSessionServiceError):
    """The declared API-key scheme injects into a header the platform reserves."""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"the API's key scheme targets the reserved header {field_name!r}")
        self.field_name = field_name


class UnpinnedServerHostError(ConnectSessionServiceError):
    """A server URL takes its host from a variable that is not limited to an enum."""

    def __init__(self, variables: list[str]) -> None:
        super().__init__(
            f"the API's servers take their host from unrestricted variables {variables!r}; "
            "a credential for it cannot be pinned to known hosts"
        )
        self.variables = variables


class SchemeChangedError(ConnectSessionServiceError):
    """The API's declared scheme changed since the session opened; the session ended."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"session {session_id!r} ended: the API's declared auth scheme changed; "
            "start a new session"
        )
        self.session_id = session_id


class ServersChangedError(ConnectSessionServiceError):
    """The API's server hosts changed since the session opened; the session ended."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"session {session_id!r} ended: the API's server hosts changed; start a new session"
        )
        self.session_id = session_id


class ReviewStaleError(ConnectSessionServiceError):
    """The confirm does not match the review the approver saw (agent or digest)."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id!r} changed since it was reviewed; review it again")
        self.session_id = session_id


class RulesRequiredError(ConnectSessionServiceError):
    """A confirm that binds an agent named no permission rules (an empty list denies all)."""

    def __init__(self) -> None:
        super().__init__("at least one permission rule is required to bind an agent")


class ConfirmKindMismatchError(ConnectSessionServiceError):
    """The confirm body's ``kind`` does not fit the session's flow or state."""

    def __init__(self, kind: str, allowed: list[str]) -> None:
        super().__init__(f"confirm kind {kind!r} does not apply to this session; use {allowed!r}")
        self.kind = kind
        self.allowed = allowed


class TooManyOpenSessionsError(ConnectSessionServiceError):
    """The agent, or its owner, already holds the maximum number of open sessions."""

    def __init__(self, scope: str, limit: int) -> None:
        super().__init__(f"the {scope} already has {limit} open connect sessions")
        self.scope = scope
        self.limit = limit


class ExistingCredentialNotFoundError(ConnectSessionServiceError):
    """The credential picked to satisfy the session is missing or not the approver's to use."""

    def __init__(self, credential_id: str) -> None:
        super().__init__(f"credential {credential_id!r} not found or not usable for this session")
        self.credential_id = credential_id


class InsufficientGrantedScopesError(ConnectSessionServiceError):
    """The existing OAuth credential was granted fewer scopes than the session asks for."""

    def __init__(self, credential_id: str, missing: list[str]) -> None:
        super().__init__(f"credential {credential_id!r} lacks requested scopes {missing!r}")
        self.credential_id = credential_id
        self.missing = missing


class OwnClientInvalidError(ConnectSessionServiceError):
    """The approver's own OAuth client is missing an endpoint or names an unsafe one."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"own OAuth client is not usable: {detail}")


class EditBindingNotFoundError(ConnectSessionServiceError):
    """The edit names a binding that does not exist, or one the caller may not edit.

    An agent's suspended binding answers the same: suspension hides the
    credential from its agent.
    """

    def __init__(self, credential_id: str) -> None:
        super().__init__(f"no credential binding for {credential_id!r} that the caller may edit")
        self.credential_id = credential_id


class NothingToChangeError(PointsAtAccessError):
    """The edit would leave the binding exactly as it is."""

    def __init__(self) -> None:
        super().__init__("the requested change is already in place; nothing to change")


class UnknownRuleError(PointsAtAccessError):
    """A ``remove`` or ``before`` names a rule the binding does not hold."""

    def __init__(self, rule_id: str) -> None:
        super().__init__(f"rule {rule_id!r} is not on this binding")
        self.rule_id = rule_id


class EditOpenError(PointsAtAccessError):
    """The binding already has an open edit session; amend that one instead."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"an edit for this binding is already open ({session_id}); amend it")
        self.session_id = session_id


class RevisionChangedError(ConnectSessionServiceError):
    """The confirm names a revision that is no longer the session's current one."""

    def __init__(self, session_id: str, current_revision_id: str | None) -> None:
        super().__init__(f"session {session_id!r} changed since it was reviewed; review it again")
        self.session_id = session_id
        self.current_revision_id = current_revision_id


class BaselineChangedError(ConnectSessionServiceError):
    """The binding changed after the edit opened; the session was rebased onto it."""

    def __init__(self, session_id: str, current_revision_id: str) -> None:
        super().__init__(
            f"the binding changed since session {session_id!r} opened; "
            "review the rebased request again"
        )
        self.session_id = session_id
        self.current_revision_id = current_revision_id


class EditConflictedError(ConnectSessionServiceError):
    """The binding changed under an open edit in a way its request can't follow.

    ``conflicts`` names each part (a rule the request names that is gone, a
    full replacement of rules that changed since). The agent amends the
    request, or the reviewer approves their own rules with ``resolve_conflicts``.
    """

    def __init__(self, session_id: str, conflicts: list[dict[str, str]]) -> None:
        super().__init__(
            f"session {session_id!r} conflicts with a change to the binding: amend it, "
            "or approve your own rules with resolve_conflicts"
        )
        self.session_id = session_id
        self.conflicts = conflicts


class RuleSetConfirmForbiddenError(ConnectSessionServiceError):
    """The edit changes a shared rule set the confirming caller does not own."""

    def __init__(self, rule_set_id: str) -> None:
        super().__init__(
            f"the edit changes shared rule set {rule_set_id!r}; only its owner or an "
            "org admin may confirm it"
        )
        self.rule_set_id = rule_set_id


class RevisionRequiredError(ConnectSessionServiceError):
    """A confirm named no ``revision_id``: it must approve the revision its reviewer saw."""

    def __init__(self) -> None:
        super().__init__("revision_id is required: confirm the revision the review shows")


class SessionClosedError(PointsAtAccessError):
    """The session was confirmed or has ended, so its ask can no longer change."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"session {session_id!r} is closed and cannot be amended; start a new request"
        )
        self.session_id = session_id


class ConnectOpenError(ConnectSessionServiceError):
    """The agent already has an open connect session for this target; amend that one."""

    def __init__(self, session_id: str, approval_url: str) -> None:
        super().__init__(
            f"a connect session for this target is already open ({session_id}); amend it"
        )
        self.session_id = session_id
        self.approval_url = approval_url


class AmendFieldsMismatchError(ConnectSessionServiceError):
    """The amend carries fields that do not apply to the session's intent."""

    def __init__(self, intent: str, fields: list[str]) -> None:
        super().__init__(f"fields {fields!r} do not apply to a {intent} session")
        self.intent = intent
        self.fields = fields


class ScopesNotSupportedError(ConnectSessionServiceError):
    """The edit adds scopes to a credential whose access the platform cannot widen.

    Only OAuth credentials connected through an authorization-code or
    device-authorization app can be re-consented with more scopes.
    """

    def __init__(self) -> None:
        super().__init__(
            "scopes can only be added to an OAuth credential connected through an "
            "authorization-code or device-authorization app"
        )


class ScopeChangeForbiddenError(ConnectSessionServiceError):
    """Changing the scopes of a credential other agents use; the caller may not."""

    def __init__(self, credential_id: str) -> None:
        super().__init__(
            f"changing the scopes of credential {credential_id!r} changes them for other bound "
            "agents; only its owner or an org admin may confirm that"
        )
        self.credential_id = credential_id
