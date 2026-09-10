"""Domain exception hierarchy for the credentials service."""

from __future__ import annotations


class CredentialServiceError(Exception):
    """Base for all credential service errors."""


class CredentialNotFoundError(CredentialServiceError):
    """Raised when a credential identified by ID does not exist."""

    def __init__(self, credential_id: str) -> None:
        super().__init__(f"Credential '{credential_id}' not found")
        self.credential_id = credential_id


class ImmutableFieldError(CredentialServiceError):
    """Raised when an update attempts to change an immutable field."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Field '{field}' is immutable and cannot be changed")
        self.field = field


class UnsupportedProviderForTypeError(CredentialServiceError):
    """Raised when a provider does not support the requested credential type."""

    def __init__(self, provider: str, credential_type: str) -> None:
        super().__init__(
            f"Provider '{provider}' does not support credential type '{credential_type}'"
        )
        self.provider = provider
        self.credential_type = credential_type


class InvalidCredentialInputError(CredentialServiceError):
    """Raised when credential input fails business-rule validation."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class AgentBindingNotFoundError(CredentialServiceError):
    """Raised when a direct agent↔credential binding does not exist (theme 5 phase 1)."""

    def __init__(self, credential_id: str, agent_id: str) -> None:
        super().__init__(f"Credential '{credential_id}' has no binding for agent '{agent_id}'")
        self.credential_id = credential_id
        self.agent_id = agent_id
