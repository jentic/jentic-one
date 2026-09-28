"""IdP adapter protocol definition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


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


@dataclass(frozen=True, slots=True)
class TokenExchangeResult:
    """Outcome of an upstream OIDC code exchange.

    Carries userinfo alongside the verified ID-token payload so provider-specific
    claim mappers can prefer ID-token claims (e.g. Google's ``hd``, which is
    delivered on the ID token, not userinfo) without a second network round-trip.
    ``id_token_claims`` is populated by adapters that own a trust anchor for the
    provider's signing keys; generic OIDC leaves it empty.
    """

    userinfo: dict[str, object]
    id_token_claims: dict[str, object] = field(default_factory=dict)


class IdpAdapter(Protocol):
    """Protocol for pluggable external identity providers."""

    def authorize_url(self, *, state: str, nonce: str, redirect_uri: str) -> str:
        """Build the upstream authorization URL for redirecting the user."""
        ...

    async def exchange_code(self, code: str, *, redirect_uri: str) -> TokenExchangeResult:
        """Exchange an upstream authorization code for userinfo + ID-token claims."""
        ...

    def map_claims(self, exchange: TokenExchangeResult) -> IdpClaims:
        """Map upstream claims to the normalized IdpClaims structure."""
        ...
