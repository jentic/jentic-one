"""DeviceFlowConnectProvider — Connect flow for standalone device-code credentials.

Fires when a user clicks Connect on a manually-created OAUTH2_DEVICE_CODE
credential. Kicks off the RFC 8628 device-authorization request against
the vendor, seeds the transient state on ``device_flow_credentials``, and
returns the ``user_code`` + ``verification_uri`` for the client to show
the human. The ``ConnectPollScanner`` picks the aux row up on the next
tick and drives the poll loop server-side — one code path shared with
the connect-session flow (Option C in the phase-2 plan).

``complete_connect`` is unreachable — device flow doesn't use a browser
redirect + callback. ``refresh`` uses the standard OAuth 2.0 refresh grant
without a client_secret (public client), matching what the broker's
``DeviceFlowHandler.on_finalise`` persisted onto ``oauth_token``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from jentic_one.control.repos.device_flow_credential_repo import (
    DeviceFlowCredentialRepository,
)
from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    NotRefreshableError,
    ProviderError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectRequest,
    ConnectState,
    DeviceCodeChallenge,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.control.services.integrations import device_flow as df
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType


class DeviceFlowConnectProvider:
    """Provider for OAUTH2_DEVICE_CODE credentials (public-client OAuth)."""

    name: str = "device_flow"

    @property
    def managed(self) -> bool:
        return False

    @property
    def supported_types(self) -> list[CredentialType]:
        return [CredentialType.OAUTH2]

    def supports(self, wire_type: CredentialType) -> bool:
        return wire_type == CredentialType.OAUTH2

    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> DeviceCodeChallenge:
        credential_id = request.extra.get("credential_id", "")
        if not credential_id:
            raise ProviderError("credential_id required in request.extra")

        async with ctx.control_db.session() as session:
            dfc = await DeviceFlowCredentialRepository.get_by_credential(session, credential_id)
            if dfc is None:
                raise NotConnectableError(
                    f"credential {credential_id!r} has no device-flow row — "
                    "was it created with grant_type=device_code?"
                )

        # RFC 8628 §3.1: POST the device_authorization endpoint. Public
        # client — no client_secret. Scopes come off the request first
        # (caller may narrow), falling back to whatever the credential
        # was created with.
        scopes = request.scopes or dfc.requested_scopes or []
        result = await df.begin_device_flow(
            authorization_endpoint=dfc.authorization_endpoint,
            client_id=dfc.client_id,
            scopes=scopes,
        )
        encrypted_device_code = ctx.encryption.encrypt(result.device_code)
        expires_at = datetime.now(UTC) + timedelta(seconds=result.expires_in)

        async with ctx.control_db.transaction() as session:
            await DeviceFlowCredentialRepository.set_transient_state(
                session,
                credential_id,
                encrypted_device_code=encrypted_device_code,
                user_code=result.user_code,
                verification_uri=result.verification_uri,
                verification_uri_complete=result.verification_uri_complete,
                poll_interval_seconds=result.interval,
                device_code_expires_at=expires_at,
                granted_scopes=list(scopes),
            )

        return DeviceCodeChallenge(
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
        # Reads the token endpoint off the credential's device_flow row.
        async with ctx.control_db.session() as session:
            dfc = await DeviceFlowCredentialRepository.get_by_credential(
                session, token.credential_id
            )
        if dfc is None:
            raise NotRefreshableError(f"credential {token.credential_id!r} has no device-flow row")
        refresh_token = await token.decrypt()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": dfc.client_id,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                dfc.token_url, data=payload, headers={"Accept": "application/json"}
            )
        if response.status_code != 200:
            raise ProviderError(
                f"device-flow refresh failed: HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            data: dict[str, str] = response.json()
        except ValueError as exc:
            raise ProviderError("device-flow token endpoint returned non-JSON") from exc

        access_token = data.get("access_token")
        if not access_token:
            raise ProviderError("device-flow refresh returned no access_token")
        expires_at: datetime | None = None
        expires_in_raw = data.get("expires_in")
        if expires_in_raw is not None:
            try:
                expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in_raw))
            except (TypeError, ValueError):
                expires_at = None
        return RefreshResult(
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
        )
