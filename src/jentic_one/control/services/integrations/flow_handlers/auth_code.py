"""Authorization-code (OAuth 2.0) handler for the connect-session state machine.

Owns the ``oauth_client_credentials`` aux row (client_id + client_secret +
authorize_url + token_url — same table a normal DirectOAuth2 credential writes
to), the state-JWT signing done at confirm time, and (later — step 4) the
server-side callback exchange. Between confirm and callback the session sits
in state=``polling``: the handler's ``poll`` reports pending, because
completion is server-driven — the human's browser lands on
``/credentials/oauth/callback`` after they consent at the vendor.

Refresh path stays on the existing ``DirectOAuth2Provider`` — auth-code
credentials produced here have the same shape as any other
``OAUTH2_AUTHORIZATION_CODE`` credential, so no broker changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.repos.oauth_client_credential_repo import (
    OAuthClientCredentialRepository,
)
from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.control.services.integrations.errors import ConnectSessionServiceError
from jentic_one.control.services.integrations.flow_handlers.base import (
    AuthCodeChallenge,
    BeginResult,
    SuccessTokens,
)
from jentic_one.shared.config import VendorAuthorizationCodeFlowConfig, VendorFlowConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import actor_type_from_id
from jentic_one.shared.models.credentials import StoredCredentialType


class AuthCodeExchangeError(ConnectSessionServiceError):
    """Raised when the vendor token endpoint refuses the authorization code."""


#: ``credential.provider`` value shared with the existing DirectOAuth2Provider —
#: keeps refresh dispatch on the same code path (no broker changes needed).
_PROVIDER_ID = "direct_oauth2"


class AuthCodeFlowHandler:
    """OAuth 2.0 authorization-code flow handler."""

    kind: ClassVar[str] = "authorization_code"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.OAUTH2_AUTHORIZATION_CODE
    provider_id: ClassVar[str] = _PROVIDER_ID

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def prepare(
        self,
        db_session: AsyncSession,
        *,
        credential_id: str,
        flow: VendorFlowConfig,
        requested_scopes: list[str],
        created_by: str,
    ) -> None:
        assert isinstance(flow, VendorAuthorizationCodeFlowConfig)
        encrypted_client_secret = self._ctx.encryption.encrypt(
            flow.client_secret.get_secret_value()
        )
        await OAuthClientCredentialRepository.create(
            db_session,
            credential_id=credential_id,
            token_url=flow.token_url,
            client_id=flow.client_id,
            encrypted_client_secret=encrypted_client_secret,
            authorize_url=flow.authorize_url,
            scope=" ".join(requested_scopes) if requested_scopes else None,
            created_by=created_by,
        )

    async def begin(
        self,
        row: ConnectSession,
        *,
        flow: VendorFlowConfig,
        confirmed_scopes: list[str],
    ) -> BeginResult:
        assert isinstance(flow, VendorAuthorizationCodeFlowConfig)

        redirect_uri = self._redirect_uri()

        # Sign a state JWT that binds the callback back to this session +
        # actor. The ``sid`` claim is what the callback route uses to decide
        # "this belongs to a connect-session" (step 4) rather than the
        # standalone-credential ConnectService path.
        connect_state = ConnectState(
            credential_id=row.credential_id,
            provider=_PROVIDER_ID,
            actor_id=row.initiator_actor_id,
            actor_type=actor_type_from_id(row.initiator_actor_id).value,
            issued_at=datetime.now(UTC),
            nonce=generate_nonce(),
            session_id=row.id,
        )
        state_secret = self._ctx.config.credentials.connect.state_secret.get_secret_value()
        ttl = self._ctx.config.credentials.connect.state_ttl_seconds
        signed_state = encode_state(state_secret, connect_state, ttl)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": flow.client_id,
            "redirect_uri": redirect_uri,
            "state": signed_state,
        }
        if confirmed_scopes:
            params["scope"] = " ".join(confirmed_scopes)
        authorize_url = f"{flow.authorize_url}?{urlencode(params)}"

        return AuthCodeChallenge(authorize_url=authorize_url)

    async def on_finalise(
        self,
        db_session: AsyncSession,
        *,
        credential_id: str,
    ) -> None:
        # No per-session transient state to clear — the oauth_client_credentials
        # row is the credential's permanent auth-code descriptor.
        return

    async def complete_from_callback(
        self,
        row: ConnectSession,
        *,
        code: str,
    ) -> SuccessTokens:
        """Exchange ``code`` for tokens at the vendor's token endpoint.

        Called by ``ConnectSessionService.complete_from_callback`` after the
        state JWT is verified and its nonce is consumed. Reads client_id +
        encrypted_client_secret + token_url from the ``oauth_client_credentials``
        row seeded at ``prepare`` time; runs a standard RFC 6749 §4.1.3
        code exchange with ``grant_type=authorization_code``.
        """
        async with self._ctx.control_db.session() as session:
            occ = await OAuthClientCredentialRepository.get_by_credential(
                session, row.credential_id
            )
            if occ is None:
                raise AuthCodeExchangeError(
                    f"no oauth_client_credentials for credential {row.credential_id!r}"
                )

        client_secret = self._ctx.encryption.decrypt(occ.encrypted_client_secret)
        redirect_uri = self._redirect_uri()

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": occ.client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                occ.token_url,
                data=payload,
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise AuthCodeExchangeError(
                f"token exchange failed: HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            data: dict[str, str] = response.json()
        except ValueError as exc:
            raise AuthCodeExchangeError("token endpoint returned non-JSON") from exc

        access_token = data.get("access_token")
        if not access_token:
            raise AuthCodeExchangeError("token endpoint returned no access_token")

        expires_in_raw = data.get("expires_in")
        expires_in: int | None
        try:
            expires_in = int(expires_in_raw) if expires_in_raw is not None else None
        except (TypeError, ValueError):
            expires_in = None

        scope = data.get("scope")
        return SuccessTokens(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_in=expires_in,
            scope=scope,
            # Auth-code: the server tells us what was granted.
            granted_scopes=scope.split() if scope else None,
        )

    def _redirect_uri(self) -> str:
        """Resolve the platform redirect URI from provider config.

        Reuses the ``credentials.providers.direct_oauth2.redirect_uri`` that
        the existing DirectOAuth2Provider registers with vendors, so the
        vendor OAuth app only ever needs one redirect_uri whitelisted for
        both entry points (connect-session flow and standalone credential
        connect). Falls back to a sensible local default when unconfigured
        — matches DirectOAuth2Provider's own posture.
        """
        provider_cfg = self._ctx.config.credentials.providers.get("direct_oauth2")
        if provider_cfg is None or not hasattr(provider_cfg, "redirect_uri"):
            raise RuntimeError(
                "credentials.providers.direct_oauth2.redirect_uri must be configured for "
                "the authorization_code connect flow"
            )
        return provider_cfg.redirect_uri
