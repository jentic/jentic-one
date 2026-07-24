"""Wire-level ↔ stored credential type mapping."""

from __future__ import annotations

from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType


def to_stored(wire: CredentialType, *, grant_type: str | None = None) -> StoredCredentialType:
    """Map a wire credential type to the internal stored type."""
    if wire == CredentialType.BEARER_TOKEN:
        return StoredCredentialType.STATIC_BEARER_TOKEN
    if wire == CredentialType.API_KEY:
        return StoredCredentialType.API_KEY
    if wire == CredentialType.BASIC:
        return StoredCredentialType.BASIC_AUTH
    if wire == CredentialType.NO_AUTH:
        return StoredCredentialType.NO_AUTH
    if wire == CredentialType.OAUTH2:
        if grant_type == "authorization_code":
            return StoredCredentialType.OAUTH2_AUTHORIZATION_CODE
        return StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS
    msg = f"Unsupported wire type: {wire}"
    raise ValueError(msg)


def to_wire(stored: StoredCredentialType) -> CredentialType:
    """Map a stored credential type back to the wire-level type."""
    if stored == StoredCredentialType.STATIC_BEARER_TOKEN:
        return CredentialType.BEARER_TOKEN
    if stored == StoredCredentialType.SESSION_TOKEN:
        return CredentialType.BEARER_TOKEN
    if stored == StoredCredentialType.API_KEY:
        return CredentialType.API_KEY
    if stored == StoredCredentialType.BASIC_AUTH:
        return CredentialType.BASIC
    if stored == StoredCredentialType.NO_AUTH:
        return CredentialType.NO_AUTH
    if stored in (
        StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        StoredCredentialType.OAUTH2_IMPLICIT,
    ):
        return CredentialType.OAUTH2
    msg = f"Unsupported stored type: {stored}"
    raise ValueError(msg)


def is_refreshable(stored: StoredCredentialType) -> bool:
    """Return True if the stored type supports token refresh."""
    return stored in (
        StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
    )
