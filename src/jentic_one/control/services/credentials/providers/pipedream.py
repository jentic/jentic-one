"""PipedreamProvider — handles OAuth2 via Pipedream Connect hosted flow."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.services.credentials.providers.direct_oauth2 import _true_expiry
from jentic_one.control.services.credentials.providers.oauth2 import parse_oauth_expires_in
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
from jentic_one.control.services.credentials.state import (
    OAUTH_CALLBACK_PATH,
    encode_state,
    generate_nonce,
)
from jentic_one.shared.config import (
    EgressConfig,
    PipedreamProviderConfig,
    bind_origin,
    resolved_control_egress,
)
from jentic_one.shared.context import Context
from jentic_one.shared.egress import build_always_pinned_transport
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.url import is_loopback_url
from jentic_one.shared.url_validation import validate_upstream_url_async

_ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class PipedreamAPIError(ProviderError):
    """Raised when a Pipedream API call fails."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Pipedream API error: HTTP {status}")


def _merge_query_params(base_url: str, params: dict[str, str]) -> str:
    """Merge *params* into *base_url*, overriding colliding query keys."""
    parts = urlsplit(base_url)
    existing = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in params
    ]
    merged = existing + list(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


async def _validated_pipedream_url(url: str, egress: EgressConfig | None) -> str:
    """Validate a Pipedream endpoint URL against HTTPS and control-plane egress policy."""
    is_prod = os.environ.get("JENTIC_ENV", "development") == "production"
    if not url.strip().lower().startswith("https://") and (is_prod or not is_loopback_url(url)):
        raise ProviderError("unsafe upstream URL: Pipedream endpoint must use https")
    try:
        return await validate_upstream_url_async(url, egress)
    except ValueError as exc:
        raise ProviderError(f"unsafe upstream URL: {exc}") from exc


def _parse_pipedream_expiry(token_data: dict[str, Any]) -> datetime | None:
    """Parse ``expires_at`` or ``expires_in`` from a Pipedream token response."""
    if "expires_at" in token_data:
        ts = parse_oauth_expires_in(token_data["expires_at"])
        try:
            return datetime.fromtimestamp(ts, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ProviderError("Invalid expires_at in token response") from exc
    return _true_expiry(token_data)


class PipedreamProvider:
    """Provider for Pipedream-hosted OAuth2 (platform stores an account reference only)."""

    name: str = "pipedream"

    def __init__(self, cfg: PipedreamProviderConfig, *, provider_id: str = "pipedream") -> None:
        self.name = provider_id
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
    def mints_from_account_ref(self) -> bool:
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
    ) -> AuthCodeChallenge:
        credential_id = request.extra.get("credential_id", "")
        if not credential_id:
            raise ProviderError("credential_id required in request.extra")

        egress = resolved_control_egress(ctx.config)
        app_token = await self._get_app_token(egress=egress)

        redirect_uri = request.redirect_uri or (
            f"{ctx.config.server.public_base_url or bind_origin(ctx.config)}{OAUTH_CALLBACK_PATH}"
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
        callback_with_state = _merge_query_params(redirect_uri, {"state": signed_state})

        connect_token_data = await self._create_connect_token(
            app_token=app_token,
            external_id=credential_id,
            success_redirect_uri=callback_with_state,
            error_redirect_uri=callback_with_state,
            egress=egress,
        )

        connect_token = connect_token_data.get("token")
        if not isinstance(connect_token, str) or not connect_token.strip():
            raise ProviderError("Pipedream connect token response missing token")
        connect_link = connect_token_data.get(
            "connect_link_url",
            f"https://pipedream.com/connect/{connect_token}",
        )
        if not isinstance(connect_link, str) or not connect_link.strip():
            raise ProviderError("Pipedream connect token response invalid connect_link_url")

        return AuthCodeChallenge(authorize_url=connect_link, state=signed_state)

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

        egress = resolved_control_egress(ctx.config)
        app_token = await self._get_app_token(egress=egress)
        account_data = await self._get_account(
            app_token=app_token,
            account_id=callback.account_id,
            egress=egress,
        )
        account_obj = (
            account_data.get("data") if isinstance(account_data.get("data"), dict) else account_data
        )
        if not isinstance(account_obj, dict):
            raise ProviderError("Malformed Pipedream account response")

        returned_id = account_obj.get("id")
        if returned_id is not None and str(returned_id) != callback.account_id:
            raise ProviderError("Pipedream account_id mismatch")

        external_id = account_obj.get("external_id") or account_obj.get("external_user_id")
        if not isinstance(external_id, str) or external_id != state.credential_id:
            raise ProviderError("Pipedream account external_id does not match credential_id")

        if account_obj.get("project_id") not in (None, self._project_id):
            raise ProviderError("Pipedream account project_id mismatch")
        if account_obj.get("environment") not in (None, self._environment):
            raise ProviderError("Pipedream account environment mismatch")

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

        egress = resolved_control_egress(ctx.config)
        app_token = await self._get_app_token(egress=egress)
        token_data = await self._get_account_token(
            app_token=app_token,
            account_id=token.provider_account_ref,
            egress=egress,
        )

        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("Pipedream token response missing access_token")

        expires_at = _parse_pipedream_expiry(token_data)

        return RefreshResult(
            access_token=access_token,
            expires_at=expires_at,
        )

    async def _get_app_token(self, *, egress: EgressConfig | None = None) -> str:
        if self._app_token is not None:
            return self._app_token

        safe_url = await _validated_pipedream_url(f"{self._base_url}/oauth/token", egress)
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=build_always_pinned_transport(egress)
            ) as client:
                response = await client.post(
                    safe_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret.get_secret_value(),
                    },
                    headers={"Accept": "application/json"},
                )
        except ValueError as exc:
            raise ProviderError(f"unsafe upstream URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Pipedream transport error: {type(exc).__name__}") from exc

        if response.status_code != 200:
            raise PipedreamAPIError(response.status_code, response.text)

        try:
            data = response.json()
        except ValueError as exc:
            raise PipedreamAPIError(response.status_code, "non-JSON response") from exc
        if not isinstance(data, dict):
            raise ProviderError("Pipedream /oauth/token returned non-object JSON")
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("Pipedream /oauth/token returned no access_token")
        self._app_token = access_token
        return self._app_token

    async def _create_connect_token(
        self,
        *,
        app_token: str,
        external_id: str,
        success_redirect_uri: str | None = None,
        error_redirect_uri: str | None = None,
        egress: EgressConfig | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, str] = {
            "project_id": self._project_id,
            "environment": self._environment,
            "external_id": external_id,
        }
        if success_redirect_uri is not None:
            payload["success_redirect_uri"] = success_redirect_uri
        if error_redirect_uri is not None:
            payload["error_redirect_uri"] = error_redirect_uri

        safe_url = await _validated_pipedream_url(f"{self._base_url}/connect/tokens", egress)
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=build_always_pinned_transport(egress)
            ) as client:
                response = await client.post(
                    safe_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {app_token}", "Accept": "application/json"},
                )
        except ValueError as exc:
            self._app_token = None
            raise ProviderError(f"unsafe upstream URL: {exc}") from exc
        except httpx.HTTPError as exc:
            self._app_token = None
            raise ProviderError(f"Pipedream transport error: {type(exc).__name__}") from exc

        if response.status_code != 200:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, response.text)

        try:
            data = response.json()
        except ValueError as exc:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, "non-JSON response") from exc
        if not isinstance(data, dict):
            self._app_token = None
            raise ProviderError("Pipedream /connect/tokens returned non-object JSON")
        return data

    async def _get_account(
        self,
        *,
        app_token: str,
        account_id: str,
        egress: EgressConfig | None = None,
    ) -> dict[str, Any]:
        if not _ACCOUNT_ID_RE.match(account_id):
            raise ProviderError(f"Invalid account_id format: {account_id!r}")

        safe_url = await _validated_pipedream_url(
            f"{self._base_url}/connect/accounts/{account_id}", egress
        )
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=build_always_pinned_transport(egress)
            ) as client:
                response = await client.get(
                    safe_url,
                    headers={
                        "Authorization": f"Bearer {app_token}",
                        "Accept": "application/json",
                    },
                )
        except ValueError as exc:
            self._app_token = None
            raise ProviderError(f"unsafe upstream URL: {exc}") from exc
        except httpx.HTTPError as exc:
            self._app_token = None
            raise ProviderError(f"Pipedream transport error: {type(exc).__name__}") from exc

        if response.status_code != 200:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, response.text)

        try:
            data = response.json()
        except ValueError as exc:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, "non-JSON response") from exc
        if not isinstance(data, dict):
            self._app_token = None
            raise ProviderError("Pipedream account endpoint returned non-object JSON")
        return data

    async def _get_account_token(
        self,
        *,
        app_token: str,
        account_id: str,
        egress: EgressConfig | None = None,
    ) -> dict[str, Any]:
        if not _ACCOUNT_ID_RE.match(account_id):
            raise ProviderError(f"Invalid account_id format: {account_id!r}")

        safe_url = await _validated_pipedream_url(
            f"{self._base_url}/connect/accounts/{account_id}/token", egress
        )
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=build_always_pinned_transport(egress)
            ) as client:
                response = await client.get(
                    safe_url,
                    headers={
                        "Authorization": f"Bearer {app_token}",
                        "Accept": "application/json",
                    },
                )
        except ValueError as exc:
            self._app_token = None
            raise ProviderError(f"unsafe upstream URL: {exc}") from exc
        except httpx.HTTPError as exc:
            self._app_token = None
            raise ProviderError(f"Pipedream transport error: {type(exc).__name__}") from exc

        if response.status_code != 200:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, response.text)

        try:
            data = response.json()
        except ValueError as exc:
            self._app_token = None
            raise PipedreamAPIError(response.status_code, "non-JSON response") from exc
        if not isinstance(data, dict):
            self._app_token = None
            raise ProviderError("Pipedream account token endpoint returned non-object JSON")
        return data
