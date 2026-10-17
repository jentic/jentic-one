"""ProviderRegistry — resolves provider names to CredentialProvider instances."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog
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

logger = structlog.get_logger(__name__)


def _build_provider(name: str, pc: ProviderConfig) -> CredentialProvider:
    """Dispatch on pc.kind to build a provider instance."""
    if isinstance(pc, DirectOAuth2ProviderConfig):
        return DirectOAuth2Provider(pc)
    if isinstance(pc, PipedreamProviderConfig):
        return PipedreamProvider(pc)
    raise UnknownProviderError(name)


class ProviderRegistry:
    """Registry mapping provider names to CredentialProvider instances.

    Immutable once built: a config change builds a new registry (reusing every
    provider whose config is unchanged) and the owner swaps it in atomically,
    so a caller that already resolved a provider keeps a consistent instance
    for the rest of its request.
    """

    def __init__(
        self,
        providers: dict[str, CredentialProvider],
        configs: dict[str, ProviderConfig] | None = None,
    ) -> None:
        self._providers = providers
        # The config each configurable provider was built from (built-ins have
        # none) — what a rebuild compares against to reuse an instance.
        self._configs: dict[str, ProviderConfig] = configs or {}

    def get(self, name: str) -> CredentialProvider:
        """Resolve a provider by name, or raise UnknownProviderError."""
        try:
            return self._providers[name]
        except KeyError:
            raise UnknownProviderError(name) from None

    def config(self, name: str) -> ProviderConfig | None:
        """The config ``name``'s provider was built from (``None`` for built-ins)."""
        return self._configs.get(name)

    def list_all(self) -> dict[str, CredentialProvider]:
        """Return all registered providers (read-only snapshot)."""
        return dict(self._providers)

    def superseded_by(self, new: ProviderRegistry) -> list[CredentialProvider]:
        """The providers held here that ``new`` no longer holds (by identity)."""
        kept = {id(p) for p in new._providers.values()}
        return [p for p in self._providers.values() if id(p) not in kept]

    @classmethod
    def from_config(cls, cfg: CredentialsConfig) -> ProviderRegistry:
        """Build a registry from configuration, always including StaticProvider."""
        return cls.from_config_and_dynamic(cfg, {})

    @classmethod
    def from_config_and_dynamic(
        cls,
        cfg: CredentialsConfig,
        dynamic: dict[str, dict[str, Any]],
        *,
        previous: ProviderRegistry | None = None,
    ) -> ProviderRegistry:
        """Build a registry from YAML config merged with runtime configs.

        ``dynamic`` maps provider name to an already-decoded config dict whose
        secret fields hold **plaintext** values (decryption happens before this
        is called). Dynamic entries override YAML entries of the same name.
        Always includes the built-in ``static`` provider.

        ``previous`` is the registry being replaced: its built-ins, and every
        provider whose config compares equal (secrets included), are carried
        over as the same instance, so a rebuild with nothing changed for a
        provider keeps whatever that provider holds in memory.
        """
        if previous is not None:
            # Only a genuine built-in carries over: a configured provider that
            # shadowed a built-in name is rebuilt (or dropped) like any other.
            providers = {
                name: previous._providers[name]
                for name in _BUILTIN_NAMES
                if name in previous._providers and name not in previous._configs
            }
        else:
            providers = {}
        builtins: dict[str, CredentialProvider] = {
            "static": StaticProvider(),
            # ``device_authorization`` is the ``credential.provider`` value written by
            # both the connect-session ``create_session`` path and the raw
            # manual-create + grant_type=device_code path — always registered
            # so ``POST /credentials/{id}/connect`` can find it.
            "device_authorization": DeviceAuthorizationConnectProvider(),
        }
        for name, provider in builtins.items():
            providers.setdefault(name, provider)
        merged: dict[str, ProviderConfig] = dict(cfg.providers)
        for name, raw in dynamic.items():
            merged[name] = _PROVIDER_CONFIG_ADAPTER.validate_python(raw)
        for name, pc in merged.items():
            if previous is not None and previous._configs.get(name) == pc:
                providers[name] = previous._providers[name]
            else:
                providers[name] = _build_provider(name, pc)
        return cls(providers, merged)


#: Providers every registry registers regardless of configuration.
_BUILTIN_NAMES: tuple[str, ...] = ("static", "device_authorization")


async def aclose_providers(providers: Iterable[CredentialProvider]) -> None:
    """Close each provider that owns resources (an ``aclose()`` coroutine method).

    ``aclose`` is optional — the built-in providers open a scoped HTTP client
    per call and hold nothing between calls. A provider that keeps a
    long-lived client implements it to release that client. A failing close
    is logged (type only, never the error text) and does not stop the rest.
    """
    for provider in providers:
        close = getattr(provider, "aclose", None)
        if not callable(close):
            continue
        try:
            await close()
        except Exception as exc:
            logger.warning(
                "credential_provider_close_failed",
                provider=getattr(provider, "name", type(provider).__name__),
                error_type=type(exc).__name__,
            )
