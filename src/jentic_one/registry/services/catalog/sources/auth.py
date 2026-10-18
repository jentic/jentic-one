"""Request credentials for catalog sources.

A ``SourceAuthResolver`` returns the headers one source's requests carry. Only
``none`` is built in, and startup validation accepts only ``none``: the source
HTTP client sends no resolver headers. Secrets are read by a resolver at request
time (from an environment variable or a secret store) and never reach the
database or logs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from jentic_one.shared.config import SourceAuthConfig


@runtime_checkable
class SourceAuthResolver(Protocol):
    """Supplies the auth headers for one source's outbound requests."""

    async def headers(self) -> Mapping[str, str]: ...


class NoAuthResolver:
    """The ``none`` resolver: requests carry no credentials."""

    async def headers(self) -> Mapping[str, str]:
        return {}


SourceAuthResolverFactory = Callable[[SourceAuthConfig], SourceAuthResolver]

_RESOLVERS: dict[str, SourceAuthResolverFactory] = {"none": lambda _cfg: NoAuthResolver()}


def register_source_auth_type(name: str, factory: SourceAuthResolverFactory) -> None:
    """Register an auth resolver type; refuses to replace a different factory."""
    existing = _RESOLVERS.get(name)
    if existing is not None and existing is not factory:
        raise ValueError(f"catalog source auth type {name!r} is already registered")
    _RESOLVERS[name] = factory


def registered_source_auth_types() -> frozenset[str]:
    """The auth types registered in this process."""
    return frozenset(_RESOLVERS)


def build_source_auth(cfg: SourceAuthConfig) -> SourceAuthResolver:
    """Build the resolver for ``cfg.type``; raises ``ValueError`` for an unknown type."""
    factory = _RESOLVERS.get(cfg.type)
    if factory is None:
        raise ValueError(
            f"unknown catalog source auth type {cfg.type!r}; registered types: {sorted(_RESOLVERS)}"
        )
    return factory(cfg)
