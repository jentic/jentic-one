"""CredentialProvider protocol and error types."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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


@runtime_checkable
class CredentialProvider(Protocol):
    """Protocol that all credential providers must satisfy."""

    name: str

    @property
    def managed(self) -> bool: ...

    @property
    def supported_types(self) -> list[CredentialType]: ...

    def supports(self, wire_type: CredentialType) -> bool: ...

    async def begin_connect(
        self,
        ctx: Context,
        *,
        api: APIReference,
        request: ConnectRequest,
    ) -> ConnectChallenge: ...

    async def complete_connect(
        self,
        ctx: Context,
        *,
        state: ConnectState,
        callback: ConnectCallback,
    ) -> ProvisionResult: ...

    async def refresh(
        self,
        ctx: Context,
        *,
        token: OAuthTokenView,
    ) -> RefreshResult: ...


class ProviderError(Exception):
    """Base error for credential provider operations."""


class NotConnectableError(ProviderError):
    """Raised when a provider does not support the connect flow."""


class NotRefreshableError(ProviderError):
    """Raised when a provider does not support token refresh."""


class InactiveRegistrationError(ProviderError):
    """Raised when a credential references an inactive ``oauth_app_registrations`` row.

    Refresh (and complete-connect) fail closed against an inactive
    registration: the operator has flipped the kill switch on this shared
    OAuth app, so no new tokens may be minted through it. Existing vaulted
    access tokens keep injecting until their expiry — this only blocks
    refresh + new grants, matching the ``is_active`` semantics.
    """


class UnknownProviderError(ProviderError):
    """Raised when a provider name cannot be resolved."""

    def __init__(self, name: str) -> None:
        self.provider_name = name
        super().__init__(f"Unknown credential provider: {name!r}")
