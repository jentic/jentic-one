"""Domain exception hierarchy for the toolkit service."""

from __future__ import annotations


class ToolkitServiceError(Exception):
    """Base for all toolkit service errors."""


class ToolkitNotFoundError(ToolkitServiceError):
    """Raised when a toolkit identified by ID does not exist."""

    def __init__(self, toolkit_id: str) -> None:
        super().__init__(f"Toolkit '{toolkit_id}' not found")
        self.toolkit_id = toolkit_id


class ToolkitAccessDeniedError(ToolkitServiceError):
    """Raised when a toolkit exists but is hidden from the caller by owner scoping.

    Distinguishes an authorization outcome from a missing row: the toolkit is
    real, but the caller neither owns it, is bound to it, nor holds ``org:admin``,
    so a write is refused. Surfacing this as ``403`` (rather than a misleading
    ``404 toolkit_not_found``) names the real requirement to the caller. See
    issue #682.
    """

    def __init__(self, toolkit_id: str) -> None:
        super().__init__(
            f"Toolkit '{toolkit_id}' exists but is not accessible; write access requires "
            "a binding to it, matching ownership, or org:admin"
        )
        self.toolkit_id = toolkit_id


class ToolkitKeyNotFoundError(ToolkitServiceError):
    """Raised when a toolkit key identified by ID does not exist."""

    def __init__(self, key_id: str) -> None:
        super().__init__(f"Toolkit key '{key_id}' not found")
        self.key_id = key_id


class BindingNotFoundError(ToolkitServiceError):
    """Raised when a toolkit-credential binding does not exist."""

    def __init__(self, toolkit_id: str, credential_id: str) -> None:
        super().__init__(
            f"Binding between toolkit '{toolkit_id}' and credential '{credential_id}' not found"
        )
        self.toolkit_id = toolkit_id
        self.credential_id = credential_id


class DuplicateBindingError(ToolkitServiceError):
    """Raised when trying to bind a credential that is already bound."""

    def __init__(self, toolkit_id: str, credential_id: str) -> None:
        super().__init__(f"Credential '{credential_id}' is already bound to toolkit '{toolkit_id}'")
        self.toolkit_id = toolkit_id
        self.credential_id = credential_id


class KeyAlreadyRevokedError(ToolkitServiceError):
    """Raised when trying to revoke a key that is already revoked."""

    def __init__(self, key_id: str) -> None:
        super().__init__(f"Key '{key_id}' is already revoked")
        self.key_id = key_id
