"""ProviderRegistry — resolves provider names to CredentialProvider instances."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from jentic_one.control.services.credentials.providers.base import (
    CredentialProvider,
    UnknownProviderError,
)
from jentic_one.control.services.credentials.providers.device_authorization import (
    DeviceAuthorizationConnectProvider,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import DirectOAuth2Provider
from jentic_one.control.services.credentials.providers.pipedream import PipedreamProvider
from jentic_one.control.services.credentials.providers.static import StaticProvider
from jentic_one.shared.config import (
    CredentialsConfig,
    DirectOAuth2ProviderConfig,
    PipedreamProviderConfig,
    ProviderConfig,
)

_PROVIDER_CONFIG_ADAPTER: TypeAdapter[ProviderConfig] = TypeAdapter(ProviderConfig)


def _build_provider(name: str, pc: ProviderConfig) -> CredentialProvider:
    """Dispatch on pc.kind to build a provider instance."""
    if isinstance(pc, DirectOAuth2ProviderConfig):
        return DirectOAuth2Provider(pc)
    if isinstance(pc, PipedreamProviderConfig):
        return PipedreamProvider(pc)
    raise UnknownProviderError(name)


class ProviderRegistry:
    """Registry mapping provider names to CredentialProvider instances."""

    def __init__(self, providers: dict[str, CredentialProvider]) -> None:
        self._providers = providers

    def get(self, name: str) -> CredentialProvider:
        """Resolve a provider by name, or raise UnknownProviderError."""
        try:
            return self._providers[name]
        except KeyError:
            raise UnknownProviderError(name) from None

    def list_all(self) -> dict[str, CredentialProvider]:
        """Return all registered providers (read-only snapshot)."""
        return dict(self._providers)

    def mints_from_account_ref(self, name: str) -> bool:
        """Whether credentials on provider *name* connect as an account reference.

        Such a credential is connected by its ``provider_account_ref`` and
        refreshes from it, with no local token row. Every other provider keeps
        its grant in ``oauth_tokens``, so a set ``provider_account_ref`` there
        is only a display name. An unknown provider is not one, so a
        credential on it fails closed as needing a reconnect.

        This is about where the grant lives. ``CredentialProvider.managed`` is
        a different question, whether the provider supplies the OAuth client
        material (``direct_oauth2`` is managed yet keeps its grant in
        ``oauth_tokens``); use this predicate, not ``.managed``, to decide
        connectedness.
        """
        try:
            return self.get(name).mints_from_account_ref
        except UnknownProviderError:
            return False

    def account_ref_provider_names(self) -> frozenset[str]:
        """Registry names for which :meth:`mints_from_account_ref` holds (for SQL filters)."""
        return frozenset(name for name in self._providers if self.mints_from_account_ref(name))

    @classmethod
    def from_config(cls, cfg: CredentialsConfig) -> ProviderRegistry:
        """Build a registry from configuration, always including StaticProvider."""
        return cls.from_config_and_dynamic(cfg, {})

    @classmethod
    def from_config_and_dynamic(
        cls,
        cfg: CredentialsConfig,
        dynamic: dict[str, dict[str, Any]],
    ) -> ProviderRegistry:
        """Build a registry from YAML config merged with runtime configs.

        ``dynamic`` maps provider name to an already-decoded config dict whose
        secret fields hold **plaintext** values (decryption happens before this
        is called). Dynamic entries override YAML entries of the same name.
        Always includes the built-in ``static`` provider.
        """
        providers: dict[str, CredentialProvider] = {
            "static": StaticProvider(),
            # ``device_authorization`` is the ``credential.provider`` value written by
            # both the connect-session ``create_session`` path and the raw
            # manual-create + grant_type=device_code path — always registered
            # so ``POST /credentials/{id}/connect`` can find it.
            "device_authorization": DeviceAuthorizationConnectProvider(),
        }
        for name, pc in cfg.providers.items():
            providers[name] = _build_provider(name, pc)
        for name, raw in dynamic.items():
            pc = _PROVIDER_CONFIG_ADAPTER.validate_python(raw)
            providers[name] = _build_provider(name, pc)
        return cls(providers)
