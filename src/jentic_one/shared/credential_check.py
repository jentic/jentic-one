"""Credential check: the result shape and the seam control calls it through.

Saving a credential stores it without ever trying it, so a bad key, a wrong
region or a missing scope first shows up as an agent's failed call several
steps later (#438, #630). A check makes one safe read call with the stored
credential and names what went wrong.

Picking that call needs the registry and authenticating it needs the broker's
injection code, so the implementation lives in the composition layer
(``jentic_one.credential_check``) and reaches control through this protocol,
the same way ``CatalogAutoImportProtocol`` does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from jentic_one.shared.auth.identity import Identity


class CredentialCheckStatus(StrEnum):
    """What one test call says about a credential."""

    OK = "ok"
    BAD_KEY = "bad_key"
    EXPIRED = "expired"
    MISSING_SCOPE = "missing_scope"
    WRONG_BASE_URL = "wrong_base_url"
    UNREACHABLE = "unreachable"
    UNTESTED = "untested"


@dataclass(frozen=True, slots=True)
class CredentialCheckResult:
    """The verdict plus the call it rests on. Never carries secret material."""

    status: CredentialCheckStatus
    reason: str
    probe: str | None = None
    """The call made, e.g. ``GET https://api.example.com/v1/me``, query values redacted."""
    upstream_status: int | None = None


class CredentialCheckerProtocol(Protocol):
    """Make one test call with a stored credential.

    The caller has already authorized ``identity`` to manage the credential.
    Implementations must not raise for an upstream or credential failure:
    that is the verdict, returned as a result.
    """

    async def check(self, *, credential_id: str, identity: Identity) -> CredentialCheckResult: ...
