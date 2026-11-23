"""DeviceAuthorizationConnectProvider — Connect flow for standalone device-code credentials.

Fires when a user clicks Connect on a manually-created OAUTH2_DEVICE_CODE
credential. Kicks off the RFC 8628 device-authorization request against
the vendor, seeds the transient state on ``device_authorization_credentials``, and
returns the ``user_code`` + ``verification_uri`` for the client to show
the human. The ``ConnectPollScanner`` picks the aux row up on the next
tick and drives the poll loop server-side — one code path shared with
the connect-session flow (Option C in the phase-2 plan).

``complete_connect`` is unreachable — device flow doesn't use a browser
redirect + callback. ``refresh`` uses the standard OAuth 2.0 refresh grant
without a client_secret (public client), matching what the broker's
``DeviceAuthorizationHandler.on_finalise`` persisted onto ``oauth_token``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from jentic_one.control.repos import ConnectNonceRepository, CredentialRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.services.credentials.providers.base import (
    InactiveRegistrationError,
    NotConnectableError,
    NotRefreshableError,
    ProviderError,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import _true_expiry
from jentic_one.control.services.credentials.providers.oauth2 import (
    OAuth2Provider,
    TokenExchangeError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectRequest,
    ConnectState,
    DeviceAuthorizationChallenge,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.control.services.credentials.state import generate_nonce
from jentic_one.control.services.credentials.token_endpoint import same_origin
from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.shared.config import resolved_control_egress
from jentic_one.shared.context import Context


class DeviceAuthorizationConnectProvider(OAuth2Provider):
    """Provider for OAUTH2_DEVICE_CODE credentials (public-client OAuth)."""

    name: str = "device_authorization"

    @property
    def managed(self) -> bool:
        return False

    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> DeviceAuthorizationChallenge:
        credential_id = request.extra.get("credential_id", "")
        if not credential_id:
            raise ProviderError("credential_id required in request.extra")

        async with ctx.control_db.session() as session:
            dfc = await DeviceAuthorizationCredentialRepository.get_by_credential(
                session, credential_id
            )
            if dfc is None:
                raise NotConnectableError(
                    f"credential {credential_id!r} has no device-flow row — "
                    "was it created with grant_type=device_code?"
                )

        actor_id = request.extra.get("actor_id")
        if not actor_id:
            raise ProviderError("device flow requires an initiating actor")

        # RFC 8628 §3.1: POST the device_authorization endpoint. Public
        # client — no client_secret. Scopes come off the request first
        # (caller may narrow), falling back to whatever the credential
        # was created with.
        scopes = request.scopes or dfc.requested_scopes or []
        result = await df.begin_device_authorization(
            authorization_endpoint=dfc.authorization_endpoint,
            client_id=dfc.client_id,
            scopes=scopes,
            egress=resolved_control_egress(ctx.config),
        )
        encrypted_device_code = ctx.encryption.encrypt(result.device_code)
        expires_at = datetime.now(UTC) + timedelta(seconds=result.expires_in)
        flow_nonce = generate_nonce()

        async with ctx.control_db.transaction() as session:
            # Serialize with session consent on the credential's sole aux row.
            # A live session owns the shared scanner path even if its vendor
            # response is already in flight, so a standalone code must never
            # replace that session's transient state.
            locked = await DeviceAuthorizationCredentialRepository.get_by_credential(
                session, credential_id, for_update=True
            )
            if locked is None:
                raise NotConnectableError(
                    f"credential {credential_id!r} has no device-flow row — "
                    "was it created with grant_type=device_code?"
                )
            live_session = await ConnectSessionRepository.get_live_by_credential(
                session, credential_id
            )
            if live_session is not None:
                raise NotConnectableError(
                    "a connect session is already in progress for this credential"
                )
            await ConnectNonceRepository.register(
                session,
                nonce=flow_nonce,
                credential_id=credential_id,
                expires_at=expires_at,
                created_by=actor_id,
            )
            await DeviceAuthorizationCredentialRepository.set_transient_state(
                session,
                credential_id,
                encrypted_device_code=encrypted_device_code,
                user_code=result.user_code,
                verification_uri=result.verification_uri,
                verification_uri_complete=result.verification_uri_complete,
                poll_interval_seconds=result.interval,
                device_code_expires_at=expires_at,
                granted_scopes=list(scopes),
                flow_nonce=flow_nonce,
            )

        return DeviceAuthorizationChallenge(
            flow_id=flow_nonce,
            user_code=result.user_code,
            verification_uri=result.verification_uri,
            verification_uri_complete=result.verification_uri_complete,
            poll_interval_seconds=result.interval,
        )

    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult:
        # Device flow completes server-side via ``ConnectPollScanner`` →
        # ``ConnectSessionService.advance_polling_credential``, not via a
        # redirect callback. Router should never dispatch here.
        raise NotConnectableError(
            "device flow completes server-side via the poll scanner, not via callback"
        )

    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult:
        # Standard OAuth 2.0 refresh with a public client — no client_secret.
        # A credential minted through a shared registration refreshes against
        # the registration's live ``token_endpoint`` / ``client_id`` so an
        # admin endpoint edit takes effect without re-connecting; the copy on
        # the credential's device_authorization row is only the fallback for
        # standalone credentials. A disabled registration fails closed,
        # mirroring ``DirectOAuth2Provider``.
        async with ctx.control_db.session() as session:
            dfc = await DeviceAuthorizationCredentialRepository.get_by_credential(
                session, token.credential_id
            )
            credential = await CredentialRepository.get_by_id(session, token.credential_id)
            registration = None
            if credential is not None and credential.oauth_app_registration_id is not None:
                registration = await OAuthAppRegistrationRepository.get_by_id(
                    session, credential.oauth_app_registration_id
                )
                if registration is not None and not registration.is_active:
                    raise InactiveRegistrationError(
                        f"oauth_app_registration {registration.id!r} is inactive — refresh refused"
                    )
        if dfc is None:
            raise NotRefreshableError(f"credential {token.credential_id!r} has no device-flow row")
        token_url, client_id = dfc.token_url, dfc.client_id
        if registration is not None:
            details = registration.device_authorization_details
            if details is None:
                raise ProviderError(
                    f"oauth_app_registration {registration.id!r} is missing its "
                    "device_authorization_details extension"
                )
            if not same_origin(details.token_endpoint, dfc.token_url):
                raise ProviderError(
                    f"oauth_app_registration {registration.id!r} token_endpoint origin "
                    "does not match the origin where the credential was minted"
                )
            token_url, client_id = details.token_endpoint, registration.client_id
        refresh_token = await token.decrypt()
        try:
            data = await self._post_token(
                token_url,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                },
                egress=resolved_control_egress(ctx.config),
            )
        except httpx.HTTPError as exc:
            raise TokenExchangeError(
                0, f"token endpoint transport error: {type(exc).__name__}"
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError("device-flow refresh returned non-object JSON")
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("device-flow refresh returned no access_token")
        expires_at = _true_expiry(data)
        new_refresh_token = data.get("refresh_token")
        scope = data.get("scope")
        return RefreshResult(
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=new_refresh_token if isinstance(new_refresh_token, str) else None,
            scope=scope if isinstance(scope, str) else None,
        )
