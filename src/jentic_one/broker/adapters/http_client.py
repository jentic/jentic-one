"""The single shared, bounded outbound ``httpx.AsyncClient``.

Infrastructure adapter (it owns the transport), **not** ``broker/core/``. One
client per process, shared by the sync handler **and** the in-process async
worker, so there is exactly one connection pool and one place to bound
connections. Lifecycle (create on startup / close on shutdown) is owned by the
app lifespan; handlers reach it only through a ``Depends()`` provider.

The accessor is a small **provider keyed on an optional client-cert fingerprint**
so the deferred mTLS scheme can register a per-cert sub-client later
without reworking the single-pool assumption. Today there is a single key (the
shared client).
"""

from __future__ import annotations

import httpx

from jentic_one.broker.adapters.egress import DnsPinningTransport
from jentic_one.shared.config import EgressConfig, UpstreamClientConfig


def build_client(
    cfg: UpstreamClientConfig, egress: EgressConfig | None = None
) -> httpx.AsyncClient:
    """Build the single bounded outbound client from config.

    ``follow_redirects=False``: the egress layer owns redirect policy; off is safe now.
    ``http2`` negotiates h2 via ALPN and falls back to 1.1 (needs the ``h2``
    package). The broker is a passthrough and must not transparently decompress
    upstream bodies (zip-bomb vector), so callers stream raw bytes and forward
    the caller's ``Accept-Encoding`` rather than httpx's injected default.

    When *egress* enables DNS pinning, the underlying transport is wrapped in a
    :class:`DnsPinningTransport` so every connection is resolved, re-validated
    against the egress policy, and pinned to the validated IP (the DNS-rebind
    guard).
    """
    limits = httpx.Limits(
        max_connections=cfg.max_connections,
        max_keepalive_connections=cfg.max_keepalive,
    )
    timeout = httpx.Timeout(
        connect=cfg.connect_timeout_s,
        read=cfg.read_timeout_s,
        write=cfg.write_timeout_s,
        pool=cfg.pool_timeout_s,
    )
    transport: httpx.AsyncBaseTransport | None = None
    if egress is not None and egress.dns_pinning_enabled:
        inner = httpx.AsyncHTTPTransport(limits=limits, http2=cfg.http2)
        transport = DnsPinningTransport(inner, egress)
    return httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        http2=cfg.http2,
        transport=transport,
    )


class HttpClientProvider:
    """Provides the outbound client, keyed on an optional client-cert fingerprint.

    v1 is HTTP-only: a single ``None`` key maps to the shared client. The keyed
    shape exists so adding mTLS later means registering a sub-client
    per client-cert identity — TLS sessions can't be reused across certs — rather
    than reworking the single-pool assumption. The provider owns close on
    shutdown.
    """

    def __init__(self, cfg: UpstreamClientConfig, egress: EgressConfig | None = None) -> None:
        self._cfg = cfg
        self._egress = egress
        self._clients: dict[str | None, httpx.AsyncClient] = {None: build_client(cfg, egress)}

    def get(self, *, cert_fingerprint: str | None = None) -> httpx.AsyncClient:
        """Return the client for the given client-cert identity (today: the shared one)."""
        client = self._clients.get(cert_fingerprint)
        if client is None:
            # No registered sub-pool for this cert yet; fall back to the shared
            # client. mTLS will register per-cert sub-clients here.
            client = self._clients[None]
        return client

    @property
    def client(self) -> httpx.AsyncClient:
        """The shared (no-cert) client — the v1 single pool."""
        return self._clients[None]

    async def aclose(self) -> None:
        """Close every sub-client. Called from the app lifespan on shutdown."""
        for client in self._clients.values():
            await client.aclose()
