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


class RateLimitExceededError(AuthServiceError):
    """Raised when a pre-auth rate limit is exceeded (429)."""

    def __init__(self, retry_after: int = 1) -> None:
        super().__init__("Too many requests")
        self.retry_after = retry_after


class ConsentAgentNotEligibleError(AuthServiceError):
    """Raised when the mint-time re-check refuses a consent→agent grant.

    ``OAuthGrantService.create_grant`` locks the agent row (``FOR UPDATE``)
    and re-checks the consent predicate — exists, ``active``, owned by the
    consenting user — INSIDE the mint transaction, closing the TOCTOU where
    an ownership transfer commits between the consent screen's unlocked
    validation read and the grant write. The consent flow maps this to the
    same human error page as a failed picker validation (never an OAuth
    redirect carrying a code, never a 500).
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' is not eligible for a consent grant")
        self.agent_id = agent_id


class OAuthGrantNotFoundError(AuthServiceError):
    """Raised when an oauth_client_grants row does not exist."""

    def __init__(self, grant_id: str) -> None:
        super().__init__(f"OAuth grant '{grant_id}' not found")
        self.grant_id = grant_id


class OAuthGrantAccessDeniedError(AuthServiceError):
    """Raised when a caller may not operate on an OAuth grant.

    Grant ``:revoke`` is owner-or-admin (design §4.8): the consenting user
    owns the grant; anyone else needs the admin permission. 403, not 404 —
    the grant id is a ksuid, not a secret.

    ``resource_id`` is the denied resource — a grant id on the ``:revoke``
    path, an agent id on the per-agent listing path (the listing gate denies
    before any grant is in hand).
    """

    def __init__(self, resource_id: str, *, message: str | None = None) -> None:
        super().__init__(message or f"Not permitted to operate on OAuth grant '{resource_id}'")
        self.resource_id = resource_id


class InvalidClientMetadataError(AuthServiceError):
    """Raised when anonymous DCR client metadata is rejected (RFC 7591 §3.2.2).

    Maps to 400 ``invalid_client_metadata`` — the RFC 7591 error code for a
    registration request whose metadata is invalid or unsupported (e.g. a
    confidential ``token_endpoint_auth_method``, an unsupported grant type, or
    a malformed redirect URI). The DCR front door only mints public clients
    (phase-3a design §4.2).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InvalidRevocationRequestError(AuthServiceError):
    """Raised when an RFC 7009 revocation request is malformed (G11).

    Maps to 400 ``invalid_request`` (RFC 7009 §2.2.1 via RFC 6749 §5.2) — the
    ONLY error arm of the revocation endpoint's form-encoded path: a request
    missing the required ``token`` parameter. Invalid, unknown, foreign, and
    already-revoked tokens are deliberately NOT errors (200 no-op, no oracle).
    """

    def __init__(self, reason: str = "token is required") -> None:
        super().__init__(reason)
        self.reason = reason
