"""Domain exception hierarchy for the auth module."""

from __future__ import annotations


class AuthServiceError(Exception):
    """Base for all auth service errors."""


class InvalidTransitionError(AuthServiceError):
    """Raised when a lifecycle verb is invalid for the current state."""

    def __init__(self, resource_id: str, current_status: str, verb: str) -> None:
        super().__init__(
            f"Cannot apply '{verb}' to resource '{resource_id}' in status '{current_status}'"
        )
        self.resource_id = resource_id
        self.current_status = current_status
        self.verb = verb


class ActorNotFoundError(AuthServiceError):
    """Raised when an actor resource does not exist."""

    def __init__(self, actor_id: str) -> None:
        super().__init__(f"Actor '{actor_id}' not found")
        self.actor_id = actor_id


class ToolkitBindingConflictError(AuthServiceError):
    """Raised when a toolkit binding already exists."""

    def __init__(self, agent_id: str, toolkit_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' is already bound to toolkit '{toolkit_id}'")
        self.agent_id = agent_id
        self.toolkit_id = toolkit_id


class ToolkitBindingNotFoundError(AuthServiceError):
    """Raised when a toolkit binding does not exist."""

    def __init__(self, agent_id: str, toolkit_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' has no binding to toolkit '{toolkit_id}'")
        self.agent_id = agent_id
        self.toolkit_id = toolkit_id


class InvalidGrantError(AuthServiceError):
    """Raised when a token grant is invalid (expired, consumed, or not found)."""

    def __init__(self, reason: str = "invalid_grant") -> None:
        super().__init__(reason)
        self.reason = reason


class UserNotAdmittedError(AuthServiceError):
    """Raised when a verified external-IdP login is not admitted by the policy.

    The IdP authenticated the user, but the deployment's admission policy declined
    to provision a brand-new account for them (e.g. invite-only or domain-gated
    deployments). Distinct from ``InvalidGrantError`` so callers/tests can tell an
    authentication failure from a policy rejection.
    """

    def __init__(self, reason: str = "user_not_admitted") -> None:
        super().__init__(reason)
        self.reason = reason


class TokenExpiredError(AuthServiceError):
    """Raised when a token has expired."""

    def __init__(self, token_id: str) -> None:
        super().__init__(f"Token '{token_id}' has expired")
        self.token_id = token_id


class RegistrationAccessDeniedError(AuthServiceError):
    """Raised when a registration access token is invalid or expired."""

    def __init__(self, reason: str = "registration_access_denied") -> None:
        super().__init__(reason)
        self.reason = reason


class OperationNotSupportedError(AuthServiceError):
    """Raised when a client management operation is not supported."""

    def __init__(self, reason: str = "operation_not_supported") -> None:
        super().__init__(reason)
        self.reason = reason


class NoApiKeyError(AuthServiceError):
    """Raised when attempting to revoke an API key that does not exist."""

    def __init__(self, actor_id: str) -> None:
        super().__init__(f"Actor '{actor_id}' has no API key to revoke")
        self.actor_id = actor_id


class InvalidOwnerError(AuthServiceError):
    """Raised when an owner_id references a non-existent user."""

    def __init__(self, owner_id: str) -> None:
        super().__init__(f"User '{owner_id}' does not exist")
        self.owner_id = owner_id


class ClaimTokenInvalidError(AuthServiceError):
    """Raised when an agent-ownership claim token is missing, wrong, or expired."""

    def __init__(self, reason: str = "invalid_claim_token") -> None:
        super().__init__(reason)
        self.reason = reason


class AgentAlreadyOwnedError(AuthServiceError):
    """Raised when claiming an agent that already has an owner."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' already has an owner")
        self.agent_id = agent_id


class ClaimActorNotAllowedError(AuthServiceError):
    """Raised when a non-user actor tries to claim agent ownership.

    ``Agent.owner_id`` is a FK to ``users.id``, so only a human user can own an
    agent. An authenticated agent/service-account/toolkit presenting the claim
    token is rejected here rather than being allowed to write a non-user id into
    the users-FK column (which would fail as an unhandled integrity error).
    """

    def __init__(self, actor_type: str) -> None:
        super().__init__(f"Actor type '{actor_type}' cannot claim agent ownership")
        self.actor_type = actor_type


class AgentWriteAccessDeniedError(AuthServiceError):
    """Raised when a non-owner, non-admin attempts a privileged agent mutation."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(
            f"Only the agent owner or an org admin may perform this action on '{agent_id}'"
        )
        self.agent_id = agent_id


class RateLimitExceededError(AuthServiceError):
    """Raised when a pre-auth rate limit is exceeded (429)."""

    def __init__(self, retry_after: int = 1) -> None:
        super().__init__("Too many requests")
        self.retry_after = retry_after
