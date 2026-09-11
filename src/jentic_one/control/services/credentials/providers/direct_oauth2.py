"""DirectOAuth2Provider — handles authorization_code and client_credentials OAuth2 flows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from jentic_one.control.repos import CredentialRepository, OAuthClientCredentialRepository
from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    ProviderError,
)
from jentic_one.control.services.credentials.providers.oauth2 import (
    InvalidGrantError,
    OAuth2Provider,
    TokenExchangeError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    AuthCodeChallenge,
    ConnectCallback,
    ConnectRequest,
    ConnectState,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.shared.config import DirectOAuth2ProviderConfig
from jentic_one.shared.context import Context

# Re-exported for existing importers of these error types through
# ``providers.direct_oauth2`` (they now live on the shared OAuth2 base).
__all__ = [
    "DirectOAuth2Provider",
    "InvalidGrantError",
    "TokenExchangeError",
]


class DirectOAuth2Provider(OAuth2Provider):
    """Provider for direct OAuth2 credentials (platform is the OAuth2 client)."""

    name: str = "direct_oauth2"

    def __init__(self, cfg: DirectOAuth2ProviderConfig) -> None:
        self._redirect_uri = cfg.redirect_uri
        self._default_scopes = cfg.default_scopes
        self._expiry_skew_seconds = cfg.expiry_skew_seconds
        self._authorize_extra_params = dict(cfg.authorize_extra_params)

    @property
    def managed(self) -> bool:
        return True

    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> AuthCodeChallenge:
        credential_id = request.extra.get("credential_id", "")
        if not credential_id:
            raise ProviderError("credential_id required in request.extra")

        async with ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None:
                raise ProviderError(f"Credential '{credential_id}' not found")

            occ = await OAuthClientCredentialRepository.get_by_credential(session, credential_id)
            if occ is None:
                raise ProviderError(f"No oauth_client_credentials for credential '{credential_id}'")

            if not occ.authorize_url:
                raise NotConnectableError(
                    "Credential has no authorize_url — cannot initiate connect flow"
                )

        scopes = request.scopes or self._default_scopes
        scope_str = " ".join(scopes) if scopes else (occ.scope or "")

        state_secret = ctx.config.credentials.connect.state_secret.get_secret_value()
        ttl = ctx.config.credentials.connect.state_ttl_seconds
        nonce = generate_nonce()

        connect_state = ConnectState(
            credential_id=credential_id,
            provider=self.name,
            actor_id=request.extra.get("actor_id"),
            actor_type=request.extra.get("actor_type"),
            issued_at=datetime.now(UTC),
            nonce=nonce,
        )
        signed_state = encode_state(state_secret, connect_state, ttl)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": occ.client_id,
            "redirect_uri": self._redirect_uri,
            "state": signed_state,
        }
        if scope_str:
            params["scope"] = scope_str
        # Apply config-supplied extras LAST so they win over every standard
        # parameter (including ``state`` and ``scope``). This is the only
        # knob general enough to accommodate non-standard IdPs, and it's
        # operator-only — misconfiguration is on the configurer.
        # Don't do it unless you really know what you're doing.
        params.update(self._authorize_extra_params)

        authorize_url = f"{occ.authorize_url}?{urlencode(params)}"
        return AuthCodeChallenge(authorize_url=authorize_url, state=signed_state)

    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult:
        if callback.error:
            raise ProviderError(f"Authorization denied: {callback.error}")

        if not callback.code:
            raise ProviderError("No authorization code in callback")

        async with ctx.control_db.session() as session:
            occ = await OAuthClientCredentialRepository.get_by_credential(
                session, state.credential_id
            )
            if occ is None:
                raise ProviderError(
                    f"No oauth_client_credentials for credential '{state.credential_id}'"
                )

        client_secret = ctx.encryption.decrypt(occ.encrypted_client_secret)

        token_data = await self._exchange_code(
            token_url=occ.token_url,
            code=callback.code,
            client_id=occ.client_id,
            client_secret=client_secret,
            redirect_uri=self._redirect_uri,
        )

        expires_at = None
        if "expires_in" in token_data:
            expires_at = datetime.now(UTC) + timedelta(
                seconds=int(token_data["expires_in"]) - self._expiry_skew_seconds
            )

        return ProvisionResult(
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            scope=token_data.get("scope"),
            provider_account_ref=None,
        )

    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult:
        async with ctx.control_db.session() as session:
            occ = await OAuthClientCredentialRepository.get_by_credential(
                session, token.credential_id
            )
            if occ is None:
                raise ProviderError(
                    f"No oauth_client_credentials for credential '{token.credential_id}'"
                )

        client_secret = ctx.encryption.decrypt(occ.encrypted_client_secret)
        refresh_token_value = await token.decrypt()

        token_data = await self._refresh_token(
            token_url=occ.token_url,
            client_id=occ.client_id,
            client_secret=client_secret,
            refresh_token=refresh_token_value,
        )

        expires_at = None
        if "expires_in" in token_data:
            expires_at = datetime.now(UTC) + timedelta(
                seconds=int(token_data["expires_in"]) - self._expiry_skew_seconds
            )

        return RefreshResult(
            access_token=token_data["access_token"],
            expires_at=expires_at,
            refresh_token=token_data.get("refresh_token"),
            scope=token_data.get("scope"),
        )

    async def _exchange_code(
        self,
        *,
        token_url: str,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict[str, str]:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        return await self._post_token(token_url, payload)

    async def _refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, str]:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        return await self._post_token(token_url, payload)
