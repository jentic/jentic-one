"""StaticProvider — handles bearer_token, api_key, and basic credentials."""

from __future__ import annotations

from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    NotRefreshableError,
)
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


class StaticProvider:
    """Provider for statically-provisioned credentials (no OAuth flow)."""

    name: str = "static"

    @property
    def managed(self) -> bool:
        return False

    @property
    def supported_types(self) -> list[CredentialType]:
        return list(CredentialType)

    def supports(self, wire_type: CredentialType) -> bool:
        return wire_type in (
            CredentialType.BEARER_TOKEN,
            CredentialType.API_KEY,
            CredentialType.BASIC,
            CredentialType.OAUTH2,
            CredentialType.NO_AUTH,
        )

    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> ConnectChallenge:
        raise NotConnectableError("StaticProvider does not support the connect flow")

    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult:
        raise NotConnectableError("StaticProvider does not support the connect flow")

    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult:
        raise NotRefreshableError("StaticProvider does not support token refresh")
