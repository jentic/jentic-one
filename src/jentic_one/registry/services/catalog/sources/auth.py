"""Request credentials for catalog sources.

A ``SourceAuthResolver`` returns the headers one source's requests carry. Built
in are ``none``, ``bearer_from_env`` and ``header_from_env``; extensions register
more with :func:`register_source_auth_type`. A resolver reads its secret on
every call (from an environment variable here, a secret store elsewhere), so the
value is never cached, never stored in the database or a job payload, and never
logged: errors name the variable, not its value.

A factory raises ``ValueError`` for a configuration it cannot use; startup
validation builds every configured resolver once, so a bad ``auth`` block (or an
unknown ``auth.type``) fails boot.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from jentic_one.registry.services.catalog.sources.protocol import SourceUnavailableError
from jentic_one.shared.config import SourceAuthConfig


@runtime_checkable
class SourceAuthResolver(Protocol):
    """Supplies the auth headers for one source's outbound requests."""

    async def headers(self) -> Mapping[str, str]: ...


class NoAuthResolver:
    """The ``none`` resolver: requests carry no credentials."""

    async def headers(self) -> Mapping[str, str]:
        return {}


#: An environment variable name (POSIX portable character set).
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: An HTTP header field name (RFC 9110 token).
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

#: Headers a source credential may not replace: they frame or route the request,
#: or identify the client.
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "te",
        "trailer",
        "keep-alive",
        "proxy-authorization",
        "user-agent",
        "if-none-match",
    }
)


class _EnvSecret:
    """Reads one environment variable on every call."""

    def __init__(self, env: str) -> None:
        self._env = env

    def read(self) -> str:
        value = os.environ.get(self._env)
        if not value or not value.strip():
            # Transient by contract: the variable may be provided on the next deploy.
            raise SourceUnavailableError(
                f"catalog source credential variable {self._env} is not set"
            )
        if "\r" in value or "\n" in value:
            raise SourceUnavailableError(
                f"catalog source credential variable {self._env} holds a line break"
            )
        return value.strip()


def _env_name(cfg: SourceAuthConfig) -> str:
    if not cfg.env:
        raise ValueError(f"auth type {cfg.type!r} needs env (the variable holding the secret)")
    if not _ENV_NAME_RE.match(cfg.env):
        # Never echo the value: a misconfigured env is often the secret itself.
        raise ValueError(
            "auth.env is not an environment variable name "
            "(it names the variable holding the secret, not the secret)"
        )
    return cfg.env


class BearerFromEnv:
    """``bearer_from_env``: ``Authorization: Bearer <value of env>``."""

    def __init__(self, cfg: SourceAuthConfig) -> None:
        if cfg.header is not None:
            raise ValueError("auth type 'bearer_from_env' takes no header")
        self._secret = _EnvSecret(_env_name(cfg))

    async def headers(self) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {self._secret.read()}"}


class HeaderFromEnv:
    """``header_from_env``: ``<header>: <value of env>`` (for example ``X-Api-Key``)."""

    def __init__(self, cfg: SourceAuthConfig) -> None:
        header = (cfg.header or "").strip()
        if not header:
            raise ValueError("auth type 'header_from_env' needs header")
        # Never echo the value: a pasted "Name: secret" would land in the error.
        if not _HEADER_NAME_RE.match(header):
            raise ValueError("auth.header is not a valid header name")
        if header.lower() in _RESERVED_HEADERS:
            raise ValueError(
                "auth.header is a reserved header; it cannot carry a source credential"
            )
        self._header = header
        self._secret = _EnvSecret(_env_name(cfg))

    async def headers(self) -> Mapping[str, str]:
        return {self._header: self._secret.read()}


def _no_auth(cfg: SourceAuthConfig) -> SourceAuthResolver:
    if cfg.env is not None or cfg.header is not None:
        raise ValueError("auth type 'none' takes no env or header")
    return NoAuthResolver()


SourceAuthResolverFactory = Callable[[SourceAuthConfig], SourceAuthResolver]

_RESOLVERS: dict[str, SourceAuthResolverFactory] = {
    "none": _no_auth,
    "bearer_from_env": BearerFromEnv,
    "header_from_env": HeaderFromEnv,
}


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
    """Build the resolver for ``cfg``; ``ValueError`` for an unknown type or a bad config."""
    factory = _RESOLVERS.get(cfg.type)
    if factory is None:
        raise ValueError(
            f"unknown catalog source auth type {cfg.type!r}; registered types: {sorted(_RESOLVERS)}"
        )
    return factory(cfg)
