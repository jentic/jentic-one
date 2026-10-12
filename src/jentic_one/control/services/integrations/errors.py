"""Errors surfaced by the ConnectSessionService.

Mapped to problem-details HTTP responses in `control/web/routers/integrations.py`.
"""

from __future__ import annotations


class ConnectSessionServiceError(Exception):
    """Base class for connect-session service errors."""


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


class RecentlyRejectedError(ConnectSessionServiceError):
    """A human rejected the agent's request for this target within the cooldown."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "a human rejected this agent's request for the same target recently; "
            "do not ask again — tell the user"
        )
        self.retry_after_seconds = retry_after_seconds


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


class ReauthorizeUnavailableError(ConnectSessionServiceError):
    """Widening the credential's scopes is not allowed (shared with other agents, or not OAuth)."""

    def __init__(self, credential_id: str, reason: str) -> None:
        super().__init__(f"credential {credential_id!r} cannot be re-authorized: {reason}")
        self.credential_id = credential_id
        self.reason = reason


class OwnClientInvalidError(ConnectSessionServiceError):
    """The approver's own OAuth client is missing an endpoint or names an unsafe one."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"own OAuth client is not usable: {detail}")
