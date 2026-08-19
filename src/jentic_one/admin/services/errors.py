"""Domain exception hierarchy for the admin module."""

from __future__ import annotations


class AdminServiceError(Exception):
    """Base for all admin service errors."""


class NotFoundError(AdminServiceError):
    """Raised when a requested resource does not exist."""


class JobNotFoundError(NotFoundError):
    """Raised when a job identified by ID does not exist."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job '{job_id}' not found")
        self.job_id = job_id


class UserNotFoundError(NotFoundError):
    """Raised when a user identified by ID does not exist."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"User '{user_id}' not found")
        self.user_id = user_id


class UserEmailNotFoundError(NotFoundError):
    """Raised when a user identified by email does not exist.

    Distinct from ``UserNotFoundError`` (keyed on id) so the message reads
    correctly and the carried attribute is named for what it holds — an email,
    not a user_id. Used by the operator password-reset CLI seam, where the
    lookup key is the email the operator typed.
    """

    def __init__(self, email: str) -> None:
        super().__init__(f"User with email '{email}' not found")
        self.email = email


class InviteTokenNotFoundError(NotFoundError):
    """Raised when an invite token does not exist."""

    def __init__(self, token_id: str) -> None:
        super().__init__(f"InviteToken '{token_id}' not found")
        self.token_id = token_id


class InviteTokenExpiredError(AdminServiceError):
    """Raised when an invite token has expired."""

    def __init__(self, token_id: str) -> None:
        super().__init__(f"InviteToken '{token_id}' has expired")
        self.token_id = token_id


class EventNotFoundError(NotFoundError):
    """Raised when an event does not exist."""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"Event '{event_id}' not found")
        self.event_id = event_id


class ConflictError(AdminServiceError):
    """Raised when an operation conflicts with existing state."""


class InvalidInputError(AdminServiceError):
    """Raised when input fails business-rule validation (beyond schema)."""


class InvalidCredentialsError(AdminServiceError):
    """Raised on authentication failure (uniform for all failure modes).

    The default message covers password login; refresh-path refusals (opaque
    token, non-user actor…) pass their own so the detail is not misleading.
    """

    def __init__(self, message: str = "Invalid email or password") -> None:
        super().__init__(message)


class AccountLockedError(AdminServiceError):
    """Raised when a user account is temporarily locked due to failed login attempts."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"Account '{user_id}' is temporarily locked")
        self.user_id = user_id


class SessionExpiredError(AdminServiceError):
    """Raised when a session refresh is refused because the absolute window elapsed.

    A login JWT can be re-minted (sliding session) only while the original
    authentication (``auth_time`` claim) is younger than
    ``admin.auth.session_ttl_seconds``. Past that, the caller must
    re-authenticate with credentials.
    """

    def __init__(self) -> None:
        super().__init__("Session has expired")


class EmailAlreadyExistsError(AdminServiceError):
    """Raised when attempting to create/update a user with a duplicate email."""

    def __init__(self, email: str) -> None:
        super().__init__(f"Email '{email}' is already in use")
        self.email = email


class SetupAlreadyCompleteError(AdminServiceError):
    """Raised when first-run admin creation is attempted after setup is done.

    The one-time bootstrap endpoint self-closes once any user exists, so a second
    create-admin attempt (e.g. an agent racing the operator) is rejected here.
    """

    def __init__(self) -> None:
        super().__init__("Setup already complete: an admin account already exists")


class UnknownPermissionError(AdminServiceError):
    """Raised when a requested permission does not exist in the catalogue."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"Unknown permission '{permission}'")
        self.permission = permission


class PermissionNotGrantableError(AdminServiceError):
    """Raised when a caller lacks the authority to grant a permission."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"Cannot grant permission '{permission}'")
        self.permission = permission


class OrgAdminGrantForbiddenError(AdminServiceError):
    """Raised when a non-org-admin attempts to grant org:admin."""

    def __init__(self) -> None:
        super().__init__("Only org:admin holders can grant org:admin")


class InviteTokenAlreadyRedeemedError(AdminServiceError):
    """Raised when attempting to redeem an already-redeemed invite token."""

    def __init__(self, token_id: str) -> None:
        super().__init__(f"InviteToken '{token_id}' has already been redeemed")
        self.token_id = token_id


class JobNotCancellableError(AdminServiceError):
    """Raised when a job cannot be cancelled (already in terminal state)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job '{job_id}' is not cancellable")
        self.job_id = job_id


class JobNotCompletedError(AdminServiceError):
    """Raised when attempting to access a result for a non-completed job."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job '{job_id}' is not completed")
        self.job_id = job_id


class JobResultExpiredError(AdminServiceError):
    """Raised when a job result has passed its retention window."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Result for job '{job_id}' has expired")
        self.job_id = job_id


class ExecutionNotFoundError(NotFoundError):
    """Raised when an execution record does not exist."""

    def __init__(self, execution_id: str) -> None:
        super().__init__(f"Execution '{execution_id}' not found")
        self.execution_id = execution_id


class ExecutionRetentionElapsedError(NotFoundError):
    """Raised when an execution record has been removed due to retention policy."""

    def __init__(self, execution_id: str) -> None:
        super().__init__(f"Execution '{execution_id}' retention period has elapsed")
        self.execution_id = execution_id


class AuditEntryNotFoundError(NotFoundError):
    """Raised when an audit entry does not exist."""

    def __init__(self, audit_id: str) -> None:
        super().__init__(f"AuditEntry '{audit_id}' not found")
        self.audit_id = audit_id


class WebhookEndpointNotFoundError(NotFoundError):
    """Raised when a webhook endpoint identified by ID does not exist."""

    def __init__(self, endpoint_id: str) -> None:
        super().__init__(f"WebhookEndpoint '{endpoint_id}' not found")
        self.endpoint_id = endpoint_id


class WebhookDeliveryNotFoundError(NotFoundError):
    """Raised when a webhook delivery identified by ID does not exist."""

    def __init__(self, delivery_id: str) -> None:
        super().__init__(f"WebhookDelivery '{delivery_id}' not found")
        self.delivery_id = delivery_id


class AgentNotFoundError(NotFoundError):
    """Raised when an agent identified by ID does not exist."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' not found")
        self.agent_id = agent_id


class ServiceAccountNotFoundError(NotFoundError):
    """Raised when a service account identified by ID does not exist."""

    def __init__(self, service_account_id: str) -> None:
        super().__init__(f"ServiceAccount '{service_account_id}' not found")
        self.service_account_id = service_account_id
