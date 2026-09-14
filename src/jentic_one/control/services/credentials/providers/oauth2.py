"""OAuth 2.0 credential-provider base class (RFC 6749 family).

Shared surface for providers whose credentials are OAuth 2.0 tokens: the
type is always ``CredentialType.OAUTH2``, and refresh + code-exchange
both round-trip a token endpoint that returns a JSON token response.

Subclasses live as *siblings* under this base (they are not related to
each other) — one per concrete flow variant:

    * ``DirectOAuth2Provider`` — authorization_code + client_credentials,
      confidential client (client_secret at the token endpoint).
    * ``DeviceAuthorizationConnectProvider`` — RFC 8628 device_code, public
      client (no client_secret).

Managed provider variants (Pipedream) intentionally stay outside this
hierarchy — they don't call an IdP's token endpoint directly, so the
shared ``_post_token`` scaffolding wouldn't apply.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectChallenge,
    ConnectRequest,
    ConnectState,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.url_validation import validate_upstream_url


class InvalidGrantError(ProviderError):
    """Raised when the IdP rejects a refresh with ``invalid_grant``."""


class TokenExchangeError(ProviderError):
    """Raised when a token-endpoint round-trip fails (non-2xx or non-JSON)."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Token exchange failed: HTTP {status}")


class OAuth2Provider(ABC):
    """Base for OAuth 2.0 credential providers."""

    name: str

    @property
    def supported_types(self) -> list[CredentialType]:
        return [CredentialType.OAUTH2]

    def supports(self, wire_type: CredentialType) -> bool:
        return wire_type == CredentialType.OAUTH2

    @property
    @abstractmethod
    def managed(self) -> bool: ...

    @abstractmethod
    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> ConnectChallenge: ...

    @abstractmethod
    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult: ...

    @abstractmethod
    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult: ...

    async def _post_token(self, token_url: str, payload: dict[str, str]) -> dict[str, str]:
        """POST ``payload`` to ``token_url`` and parse the JSON response.

        Maps the two structured failure modes both concrete flows care
        about: an ``invalid_grant`` body (revoked/expired refresh token)
        surfaces as ``InvalidGrantError`` so callers can distinguish it
        from a transient upstream fault; every other non-200 (or a body
        that isn't JSON) becomes ``TokenExchangeError`` carrying the raw
        HTTP status for logging.
        """
        # Defense-in-depth SSRF guard: ``token_url`` comes from the DB
        # (``oauth_client_credentials`` row created at credential-create time,
        # user-supplied). A tampered / misconfigured row must not be able to
        # aim this call at a private / metadata target.
        try:
            safe_url = validate_upstream_url(token_url)
        except ValueError as exc:
            raise TokenExchangeError(0, f"unsafe upstream URL: {exc}") from exc

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                safe_url,
                data=payload,
                headers={"Accept": "application/json"},
            )

        if response.status_code != 200:
            body = response.text
            if "invalid_grant" in body:
                raise InvalidGrantError("Refresh token has been revoked or expired")
            raise TokenExchangeError(response.status_code, body)

        try:
            data: dict[str, str] = response.json()
        except ValueError as exc:
            raise TokenExchangeError(response.status_code, response.text) from exc
        return data
