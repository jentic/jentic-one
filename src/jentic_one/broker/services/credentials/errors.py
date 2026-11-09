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
    # The candidate's stored OpenAPI server-variable scope, when it is the axis
    # that distinguishes it (a region-scoped credential in a
    # ``credential_server_variable_mismatch`` body). ``None`` on an unscoped
    # candidate and in the 409 ambiguity body, which turns on name/id instead.
    server_variables: dict[str, str] | None = None


class CredentialResolutionError(Exception):
    """Base for all credential resolution errors."""


class CredentialNotProvisionedError(CredentialResolutionError):
    """No credential found for the given API tuple."""

    def __init__(self, vendor: str, name: str, version: str) -> None:
        super().__init__(f"No active credential provisioned for api ({vendor}, {name}, {version})")
        self.vendor = vendor
        self.name = name
        self.version = version


class CredentialServerVariableMismatchError(CredentialResolutionError):
    """A covering credential exists, but its ``server_variables`` scope disagrees.

    A sibling of :class:`CredentialNotProvisionedError`, not a subclass: the
    orchestrator maps each to its own problem ``type``, and inheriting would
    make the mapping depend on ``except`` ordering.

    ``requested`` is the request URL's own resolved server-variable values and
    ``conflicting`` the names that disagree. ``unresolved`` marks the
    fail-closed case: the registry could not determine the URL's values, so
    every scoped credential was ruled out without a value to compare — a
    different cause with a different fix, so it is not folded in here.
    """

    def __init__(
        self,
        vendor: str,
        name: str,
        version: str,
        *,
        requested: dict[str, str],
        conflicting: list[str],
        candidates: list[CredentialCandidate],
        unresolved: bool = False,
    ) -> None:
        if unresolved:
            super().__init__(
                f"The request URL does not determine the server variables that "
                f"{len(candidates)} credential(s) for api ({vendor}, {name}, {version}) are "
                f"scoped to"
            )
        else:
            scoped = ", ".join(f"{k}={v!r}" for k, v in sorted(requested.items()))
            super().__init__(
                f"No credential for api ({vendor}, {name}, {version}) is scoped to {scoped}"
            )
        self.vendor = vendor
        self.name = name
        self.version = version
        self.requested = requested
        self.conflicting = conflicting
        self.candidates = candidates
        self.unresolved = unresolved


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


class CredentialIdNotFoundError(CredentialResolutionError):
    """The requested credential id does not match any allowed, covering candidate.

    The direct-binding twin of :class:`CredentialNameNotFoundError`: raised when
    a ``Jentic-Credential-Id`` tie-breaker names a credential that is not among
    the caller's covering candidates (unbound, suspended, inactive, or simply
    not covering this API).
    """

    def __init__(
        self,
        vendor: str,
        name: str,
        version: str,
        requested_id: str,
        candidates: list[CredentialCandidate],
    ) -> None:
        candidate_ids = [c.id for c in candidates]
        super().__init__(
            f"No bound credential with id '{requested_id}' for api ({vendor}, {name}, "
            f"{version}); available: {candidate_ids}"
        )
        self.vendor = vendor
        self.name = name
        self.version = version
        self.requested_id = requested_id
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
    """The IdP returned a transient error (5xx/timeout) during refresh.

    ``detail`` is surfaced to the caller verbatim (via the broker's 502), so it
    must never carry raw exception text — pass a safe label such as the
    exception class name.
    """

    def __init__(self, credential_id: str, detail: str = "") -> None:
        msg = f"Transient error refreshing credential '{credential_id}'"
        if detail:
            msg += f" ({detail})"
        super().__init__(credential_id, msg)
