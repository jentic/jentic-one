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
    """

    def __init__(self, registration_id: str, reason: str) -> None:
        super().__init__(f"oauth_app_registration {registration_id!r} is not usable: {reason}")
        self.registration_id = registration_id
        self.reason = reason


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
