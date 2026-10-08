"""Domain exceptions for the OAuth app registration service."""

from __future__ import annotations


class OAuthAppRegistrationServiceError(Exception):
    """Base error for the OAuth app registration service."""


class OAuthAppRegistrationNotFoundError(OAuthAppRegistrationServiceError):
    """Raised when a registration id does not exist."""

    def __init__(self, registration_id: str) -> None:
        super().__init__(f"OAuth app registration '{registration_id}' not found")
        self.registration_id = registration_id


class OAuthAppRegistrationInUseError(OAuthAppRegistrationServiceError):
    """Raised when a delete is attempted while credentials still reference the row."""

    def __init__(self, registration_id: str, credential_count: int) -> None:
        super().__init__(
            f"OAuth app registration '{registration_id}' is referenced by "
            f"{credential_count} credential(s); revoke or migrate them first"
        )
        self.registration_id = registration_id
        self.credential_count = credential_count


class InvalidOAuthAppRegistrationInputError(OAuthAppRegistrationServiceError):
    """Raised when the caller supplied fields inconsistent with the flow kind."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class SecretRotationNotSupportedError(OAuthAppRegistrationServiceError):
    """Raised when rotate-secret is called on a device-flow registration.

    Device flow (RFC 8628) is a public client — there is no client secret to
    rotate.
    """

    def __init__(self, registration_id: str) -> None:
        super().__init__(
            f"OAuth app registration '{registration_id}' has no client secret to rotate "
            "(device flow is a public client)"
        )
        self.registration_id = registration_id
