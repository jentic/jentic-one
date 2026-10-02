"""IdP adapter protocol definition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


def parse_email_verified(value: object) -> bool:
    """Interpret an IdP ``email_verified`` claim strictly.

    Only the JSON boolean ``true`` or the string ``"true"`` (case-insensitive,
    surrounding whitespace ignored) count as verified. Some providers serialise
    the claim as a string, so a plain ``bool(value)`` would treat ``"false"``
    as verified; anything else (missing, ``None``, ``"false"``, ``1``, …) is
    unverified.
    """
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


@dataclass(frozen=True, slots=True)
class IdpClaims:
    """Normalized claims returned from an external IdP."""

    external_subject: str
    email: str
    first_name: str
    last_name: str
    email_verified: bool = False
    # Google's `hd` (hosted-domain) claim, present only for Google Workspace
    # accounts. None for generic OIDC providers and consumer Google accounts.
    hosted_domain: str | None = None


class IdpAdapter(Protocol):
    """Protocol for pluggable external identity providers."""

    def authorize_url(self, *, state: str, nonce: str, redirect_uri: str) -> str:
        """Build the upstream authorization URL for redirecting the user."""
        ...

    async def exchange_code(self, code: str, *, redirect_uri: str) -> dict[str, object]:
        """Exchange an upstream authorization code for tokens/userinfo."""
        ...

    def map_claims(self, userinfo: dict[str, object]) -> IdpClaims:
        """Map upstream claims to the normalized IdpClaims structure."""
        ...
