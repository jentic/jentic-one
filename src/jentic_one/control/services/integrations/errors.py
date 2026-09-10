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


class NoOpForFlowError(ConnectSessionServiceError):
    """The resolved flow is not yet implemented in phase 1."""

    def __init__(self, flow: str) -> None:
        super().__init__(f"flow {flow!r} not supported in phase 1")
        self.flow = flow
