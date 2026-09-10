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


class RuleSetNotFoundError(CredentialServiceError):
    """Raised when a permission rule set identified by ID does not exist."""

    def __init__(self, rule_set_id: str) -> None:
        super().__init__(f"Permission rule set '{rule_set_id}' not found")
        self.rule_set_id = rule_set_id


class RuleSetNameConflictError(CredentialServiceError):
    """Raised when a rule set name is already taken (names are unique)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"A permission rule set named '{name}' already exists")
        self.name = name


class RuleSetInUseError(CredentialServiceError):
    """Raised when deleting a rule set that bindings still reference."""

    def __init__(self, rule_set_id: str, binding_count: int) -> None:
        super().__init__(
            f"Permission rule set '{rule_set_id}' is referenced by "
            f"{binding_count} binding(s); detach them first"
        )
        self.rule_set_id = rule_set_id
        self.binding_count = binding_count


class RuleSetAccessDeniedError(CredentialServiceError):
    """Raised when a caller may see but not mutate a shared rule set.

    Provisional creator-or-admin write gate pending the theme plan's open
    ownership question (OQ-6); widening it later needs no schema change.
    """

    def __init__(self, rule_set_id: str) -> None:
        super().__init__(
            f"Only the creator or an org admin may modify permission rule set '{rule_set_id}'"
        )
        self.rule_set_id = rule_set_id
