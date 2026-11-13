"""DirectOAuth2Provider — handles authorization_code and client_credentials OAuth2 flows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.repos import CredentialRepository, OAuthClientCredentialRepository
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.services.credentials.providers.base import (
    InactiveRegistrationError,
    NotConnectableError,
    ProviderError,
)
from jentic_one.control.services.credentials.providers.oauth2 import (
    InvalidGrantError,
    OAuth2Provider,
    TokenExchangeError,
    true_oauth_expiry,
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
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    EgressConfig,
    resolved_control_egress,
)
from jentic_one.shared.context import Context

# Re-exported for existing importers of these error types through
# ``providers.direct_oauth2`` (they now live on the shared OAuth2 base).
__all__ = [
    "DirectOAuth2Provider",
    "InactiveRegistrationError",
    "InvalidGrantError",
    "TokenExchangeError",
]


def _true_expiry(token_data: dict[str, Any]) -> datetime | None:
    """The access token's true expiry from an RFC 6749 ``expires_in`` response.

    No safety margin is subtracted: the broker's refresher applies
    ``expiry_skew_seconds`` once, at comparison time.
    """
    return true_oauth_expiry(token_data)


def _merge_authorize_url(base_url: str, params: dict[str, str]) -> str:
    """Merge *params* into *base_url*, overriding colliding endpoint query keys."""
    parts = urlsplit(base_url)
    existing = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in params
    ]
    merged = existing + list(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


class DirectOAuth2Provider(OAuth2Provider):
    """Provider for direct OAuth2 credentials (platform is the OAuth2 client)."""

    name: str = "direct_oauth2"

    def __init__(
        self, cfg: DirectOAuth2ProviderConfig, *, provider_id: str = "direct_oauth2"
    ) -> None:
        self.name = provider_id
        # Explicit override; when None, begin_connect derives per request.
        self._configured_redirect_uri = cfg.redirect_uri
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

        # Resolve (client_id, authorize_url, default_scope_str) with the same
        # registration-first / legacy-fallback shape ``_resolve_client_material``
        # uses for the token-endpoint side. Kept inline rather than sharing a
        # helper because the two paths need different fields off each source.
        async with ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None:
                raise ProviderError(f"Credential '{credential_id}' not found")

            client_id_value: str
            authorize_url: str
            default_scope_str: str

            if credential.oauth_app_registration_id is not None:
                registration = await OAuthAppRegistrationRepository.get_by_id(
                    session, credential.oauth_app_registration_id
                )
                if registration is None:
                    raise ProviderError(
                        f"Credential '{credential_id}' references missing "
                        f"oauth_app_registration '{credential.oauth_app_registration_id}'"
                    )
                if not registration.is_active:
                    raise InactiveRegistrationError(
                        f"oauth_app_registration {registration.id!r} is inactive — "
                        "refuse to begin a new connect through a disabled shared app"
                    )
                ac = registration.authorization_code_details
                if ac is None:
                    raise ProviderError(
                        f"oauth_app_registration {registration.id!r} is missing its "
                        "authorization_code_details extension"
                    )
                client_id_value = registration.client_id
                authorize_url = ac.authorize_url
                default_scope_str = " ".join(ac.default_scopes) if ac.default_scopes else ""
            else:
                occ = await OAuthClientCredentialRepository.get_by_credential(
                    session, credential_id
                )
                if occ is None:
                    raise ProviderError(
                        f"No oauth_client_credentials for credential '{credential_id}'"
                    )
                if not occ.authorize_url:
                    raise NotConnectableError(
                        "Credential has no authorize_url — cannot initiate connect flow"
                    )
                client_id_value = occ.client_id
                authorize_url = occ.authorize_url
                default_scope_str = occ.scope or ""

        scopes = request.scopes or self._default_scopes
        scope_str = " ".join(scopes) if scopes else default_scope_str

        # Explicit config wins; otherwise use the callback URL the web layer
        # derived for this request (public_base_url or request origin). The
        # resolved value is embedded in the signed state so the token exchange
        # replays it byte-identically (RFC 6749 §4.1.3).
        redirect_uri = self._configured_redirect_uri or request.redirect_uri
        if not redirect_uri:
            raise ProviderError(
                "No redirect_uri available: set providers.direct_oauth2.redirect_uri "
                "or server.public_base_url, or call via the web connect endpoint"
            )

        state_secret = ctx.config.credentials.connect.state_secret.get_secret_value()
        ttl = ctx.config.credentials.connect.state_ttl_seconds
        nonce = generate_nonce()

        provider_id = request.extra.get("provider_id") or self.name
        connect_state = ConnectState(
            credential_id=credential_id,
            provider=provider_id,
            actor_id=request.extra.get("actor_id"),
            actor_type=request.extra.get("actor_type"),
            issued_at=datetime.now(UTC),
            nonce=nonce,
            redirect_uri=redirect_uri,
            browser_binding_hash=request.browser_binding_hash,
            browser_host=request.browser_host,
        )
        signed_state = encode_state(state_secret, connect_state, ttl)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": client_id_value,
            "redirect_uri": redirect_uri,
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

        return AuthCodeChallenge(
            authorize_url=_merge_authorize_url(authorize_url, params),
            state=signed_state,
        )

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

        client_id, token_url, client_secret = await self._resolve_client_material(
            ctx, credential_id=state.credential_id
        )

        # Replay the exact redirect_uri the authorize request used (carried in
        # the signed state); RFC 6749 requires the token exchange to match. Fall
        # back to the configured value for states minted before this claim
        # existed (rolling upgrade).
        redirect_uri = state.redirect_uri or self._configured_redirect_uri
        if not redirect_uri:
            raise ProviderError("Connect state is missing the redirect_uri")

        token_data = await self._exchange_code(
            token_url=token_url,
            egress=resolved_control_egress(ctx.config),
            code=callback.code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )

        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("Token endpoint returned no access_token")

        expires_at = _true_expiry(token_data)
        refresh_token = token_data.get("refresh_token")
        scope = token_data.get("scope")

        return ProvisionResult(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_at=expires_at,
            scope=scope if isinstance(scope, str) else None,
            provider_account_ref=None,
            token_url=token_url,
        )

    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult:
        client_id, token_url, client_secret = await self._resolve_client_material(
            ctx, credential_id=token.credential_id
        )
        refresh_token_value = await token.decrypt()

        token_data = await self._refresh_token(
            token_url=token_url,
            egress=resolved_control_egress(ctx.config),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token_value,
        )

        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("Token endpoint returned no access_token")

        expires_at = _true_expiry(token_data)
        new_refresh_token = token_data.get("refresh_token")
        scope = token_data.get("scope")

        return RefreshResult(
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=new_refresh_token if isinstance(new_refresh_token, str) else None,
            scope=scope if isinstance(scope, str) else None,
        )

    async def _resolve_client_material(
        self,
        ctx: Context,
        *,
        credential_id: str,
    ) -> tuple[str, str, str]:
        """Return ``(client_id, token_url, client_secret)`` for a credential.

        Prefers the shared ``oauth_app_registrations`` row when the credential's
        ``oauth_app_registration_id`` is set (mints for this credential are
        going through admin-registered material). Falls back to the legacy
        embedded ``oauth_client_credentials`` row when the FK is null.

        A referenced registration that has been flipped ``is_active=False``
        is refused loudly: refresh / new grants must not silently continue
        through a disabled shared app, and the operator flipped the switch
        expecting exactly that behaviour.
        """
        async with ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None:
                raise ProviderError(f"Credential {credential_id!r} not found")
            registration: OAuthAppRegistration | None = None
            if credential.oauth_app_registration_id is not None:
                registration = await OAuthAppRegistrationRepository.get_by_id(
                    session, credential.oauth_app_registration_id
                )
            if registration is not None:
                if not registration.is_active:
                    raise InactiveRegistrationError(
                        f"oauth_app_registration {registration.id!r} is inactive — "
                        "refuse to mint / refresh through a disabled shared app"
                    )
                ac = registration.authorization_code_details
                if ac is None:
                    raise ProviderError(
                        f"oauth_app_registration {registration.id!r} is missing its "
                        "authorization_code_details extension"
                    )
                client_secret = ctx.encryption.decrypt(ac.encrypted_client_secret)
                return registration.client_id, ac.token_url, client_secret

            occ = await OAuthClientCredentialRepository.get_by_credential(session, credential_id)
            if occ is None:
                raise ProviderError(f"No oauth_client_credentials for credential {credential_id!r}")
            client_secret = ctx.encryption.decrypt(occ.encrypted_client_secret)
            return occ.client_id, occ.token_url, client_secret

    async def _exchange_code(
        self,
        *,
        token_url: str,
        egress: EgressConfig,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        return await self._post_token_checked(token_url, payload, egress=egress)

    async def _refresh_token(
        self,
        *,
        token_url: str,
        egress: EgressConfig,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        return await self._post_token_checked(token_url, payload, egress=egress)

    async def _post_token_checked(
        self,
        token_url: str,
        payload: dict[str, str],
        *,
        egress: EgressConfig,
    ) -> dict[str, Any]:
        try:
            data = await self._post_token(token_url, payload, egress=egress)
        except httpx.HTTPError as exc:
            raise TokenExchangeError(
                0, f"token endpoint transport error: {type(exc).__name__}"
            ) from exc
        if not isinstance(data, dict):
            raise TokenExchangeError(200, "token endpoint returned non-object JSON")
        return data
