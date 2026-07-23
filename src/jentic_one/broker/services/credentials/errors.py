"""Credential resolution errors for the broker execution seam."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CredentialCandidate(BaseModel):
    """A distinguishable ambiguity candidate for a 409 body (issue #643).

    Bare names collide when two credentials share a name, so a candidate also
    carries its stable ``id``, a ``last4`` display hint (last four chars of the
    id — never the secret), and ``created_at`` so an operator/agent can tell the
    two apart and pick which to remove.
    """

    id: str
    name: str
    last4: str
    created_at: datetime | None = None


class CredentialResolutionError(Exception):
    """Base for all credential resolution errors."""


class CredentialNotProvisionedError(CredentialResolutionError):
    """No credential found for the given API tuple."""

    def __init__(self, vendor: str, name: str, version: str) -> None:
        super().__init__(f"No active credential provisioned for api ({vendor}, {name}, {version})")
        self.vendor = vendor
        self.name = name
        self.version = version


class AmbiguousCredentialError(CredentialResolutionError):
    """Multiple active credentials match the API tuple."""

    def __init__(
        self,
        vendor: str,
        name: str,
        version: str,
        count: int,
        candidates: list[CredentialCandidate] | None = None,
    ) -> None:
        super().__init__(
            f"Ambiguous: {count} active credentials match api ({vendor}, {name}, {version})"
        )
        self.vendor = vendor
        self.name = name
        self.version = version
        self.count = count
        self.candidates: list[CredentialCandidate] = candidates or []


class CredentialNameNotFoundError(CredentialResolutionError):
    """The requested credential name does not match any active candidate."""

    def __init__(
        self,
        vendor: str,
        name: str,
        version: str,
        requested_name: str,
        candidates: list[CredentialCandidate],
    ) -> None:
        candidate_names = [c.name for c in candidates]
        super().__init__(
            f"No credential named '{requested_name}' for api ({vendor}, {name}, {version}); "
            f"available: {candidate_names}"
        )
        self.vendor = vendor
        self.name = name
        self.version = version
        self.requested_name = requested_name
        self.candidates = candidates


class RefreshError(CredentialResolutionError):
    """Base for errors during token refresh."""

    def __init__(self, credential_id: str, message: str) -> None:
        super().__init__(message)
        self.credential_id = credential_id


class RefreshInvalidGrantError(RefreshError):
    """The refresh token has been revoked or the user has disconnected — needs re-connect."""

    def __init__(self, credential_id: str) -> None:
        super().__init__(
            credential_id,
            f"OAuth2 credential '{credential_id}' requires re-connect (grant revoked)",
        )


class RefreshTransientError(RefreshError):
    """The IdP returned a transient error (5xx/timeout) during refresh."""

    def __init__(self, credential_id: str, detail: str = "") -> None:
        msg = f"Transient error refreshing credential '{credential_id}'"
        if detail:
            msg += f": {detail}"
        super().__init__(credential_id, msg)
