"""PipedreamProvider — handles OAuth2 via Pipedream Connect hosted flow."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

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
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.shared.config import PipedreamProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType

_ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class PipedreamAPIError(ProviderError):
    """Raised when a Pipedream API call fails."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Pipedream API error: HTTP {status}")


class PipedreamProvider:
    """Provider for Pipedream-hosted OAuth2 (platform stores an account reference only)."""

    name: str = "pipedream"

    def __init__(self, cfg: PipedreamProviderConfig) -> None:
        self._project_id = cfg.project_id
        self._environment = cfg.environment
        self._client_id = cfg.client_id
        self._client_secret = cfg.client_secret
        self._base_url = cfg.connect_base_url.rstrip("/")
        self._expiry_skew_seconds = cfg.expiry_skew_seconds
        self._app_token: str | None = None

    @property
    def managed(self) -> bool:
        return True

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
    ) -> ConnectChallenge:
        credential_id = request.extra.get("credential_id", "")
        if not credential_id:
            raise ProviderError("credential_id required in request.extra")

        app_token = await self._get_app_token()

        connect_token_data = await self._create_connect_token(
            app_token=app_token,
            external_id=credential_id,
        )

        connect_token = connect_token_data["token"]
        connect_link = connect_token_data.get(
            "connect_link_url",
            f"https://pipedream.com/connect/{connect_token}",
        )

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

        return ConnectChallenge(authorize_url=connect_link, state=signed_state)

    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult:
        if callback.error:
            raise ProviderError(f"Authorization denied: {callback.error}")

        if not callback.account_id:
            raise ProviderError("No account_id in callback")

        if not _ACCOUNT_ID_RE.match(callback.account_id):
            raise ProviderError(f"Invalid account_id format: {callback.account_id!r}")

        return ProvisionResult(
            access_token=None,
            refresh_token=None,
            expires_at=None,
            scope=None,
            provider_account_ref=callback.account_id,
        )

    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult:
        if not token.provider_account_ref:
            raise ProviderError("No provider_account_ref for Pipedream token refresh")

        app_token = await self._get_app_token()
        account = await self._get_account(
            app_token=app_token,
            account_id=token.provider_account_ref,
        )

        credentials = account.get("credentials") or {}
        # OAuth apps (BYO client) expose oauth_access_token; key-based apps
        # (e.g. Stripe) expose app-specific fields — fall back through the
        # common shapes so both kinds inject.
        access_token = (
            credentials.get("oauth_access_token")
            or credentials.get("api_key")
            or credentials.get("access_token")
            or credentials.get("token")
        )
        if not access_token:
            raise ProviderError(
                "Pipedream returned no usable credential for account "
                f"{token.provider_account_ref!r} (fields: {sorted(credentials)}). "
                "OAuth apps require your own OAuth client on Pipedream to expose "
                "credentials."
            )

        expires_at = None
        raw_expiry = account.get("expires_at")
        if raw_expiry:
            try:
                parsed = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
                expires_at = parsed - timedelta(seconds=self._expiry_skew_seconds)
            except ValueError:
                expires_at = None

        return RefreshResult(
            access_token=access_token,
            expires_at=expires_at,
        )

    async def _get_app_token(self) -> str:
        if self._app_token is not None:
            return self._app_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                },
                headers={"Accept": "application/json"},
            )

        if response.status_code != 200:
            raise PipedreamAPIError(response.status_code, response.text)

        data = response.json()
        self._app_token = data["access_token"]
        return self._app_token

    async def _create_connect_token(
        self,
        *,
        app_token: str,
        external_id: str,
    ) -> dict[str, str]:
        # Current Connect API shape: project id in the path, environment in
        # the x-pd-environment header, and the user key is external_user_id.
        payload = {"external_user_id": external_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/connect/{self._project_id}/tokens",
                json=payload,
                headers={
                    "Authorization": f"Bearer {app_token}",
                    "Accept": "application/json",
                    "x-pd-environment": self._environment,
                },
            )

        if response.status_code != 200:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, response.text)

        data: dict[str, str] = response.json()
        return data

    async def _get_account(
        self,
        *,
        app_token: str,
        account_id: str,
    ) -> dict:
        if not _ACCOUNT_ID_RE.match(account_id):
            raise ProviderError(f"Invalid account_id format: {account_id!r}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/connect/{self._project_id}/accounts/{account_id}",
                params={"include_credentials": "true"},
                headers={
                    "Authorization": f"Bearer {app_token}",
                    "Accept": "application/json",
                    "x-pd-environment": self._environment,
                },
            )

        if response.status_code != 200:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, response.text)

        data: dict = response.json()
        # Some API versions envelope the account under "data".
        if isinstance(data.get("data"), dict):
            return data["data"]
        return data
