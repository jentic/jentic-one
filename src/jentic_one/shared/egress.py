"""Shared egress safety: connection-time DNS-rebinding guard + pin (§08 E2).

``validate_upstream_url`` (the pre-request check in ``shared/url_validation``)
resolves the host and rejects a name that resolves into a blocked range. But that
check runs *before* the request; the client's own ``httpx`` connection re-resolves
the name at connect time, leaving a **TOCTOU rebind window** — a hostile resolver
can answer "public" for the validation probe and "private" (``169.254.169.254``,
``10.x``) for the real connection.

:class:`DnsPinningTransport` closes that window: on every request it resolves the
host **once**, re-validates each returned IP against the *same* egress policy
(``assert_ip_allowed`` — identical block rules, metadata hard-deny, and allowlist
exemptions), and **pins** the connection to the validated IP by rewriting the URL
host to that IP while preserving the original ``Host`` header and TLS SNI. A second
resolution can't swap in a private address because there is no second resolution.

This lives in ``shared`` because *every* outbound-fetch site needs it, not just the
broker's execution runtime: the Flow-3 catalog/ingest fetchers pull upstream specs
on server-initiated schedules and are equally exposed to a rebind. ``build_client``
(broker) and the registry fetchers both wrap their transports with this.
"""

from __future__ import annotations

import ipaddress
import socket

import httpx

from jentic_one.shared.config import EgressConfig
from jentic_one.shared.url_validation import assert_ip_allowed

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def resolve_and_validate(host: str, egress: EgressConfig | None) -> _IpAddress:
    """Resolve *host* and return the first IP that passes the egress policy.

    Validates **every** resolved address against ``assert_ip_allowed`` so a
    multi-record answer can't smuggle a blocked IP past the check, then returns
    the first one to pin the connection to. Raises ``ValueError`` if the host
    can't be resolved or any resolved IP is blocked (the rebind guard).
    """
    try:
        results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"upstream host did not resolve: {host}") from exc

    addrs = [ipaddress.ip_address(sockaddr[0]) for *_rest, sockaddr in results]
    if not addrs:
        raise ValueError(f"upstream host did not resolve: {host}")

    for addr in addrs:
        # A DNS name re-resolved here, so pass the hostname for the suffix
        # exemption — matches the validation-time semantics.
        assert_ip_allowed(addr, egress, hostname=host)
    return addrs[0]


class DnsPinningTransport(httpx.AsyncBaseTransport):
    """Wraps a transport to resolve+validate+pin the host IP per request.

    An IP-literal host is passed through unchanged (nothing to rebind). For a DNS
    name the request URL host is rewritten to the validated IP, the original host
    is preserved as the ``Host`` header, and ``sni_hostname`` is set so TLS still
    validates against the real certificate name.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, egress: EgressConfig | None) -> None:
        self._inner = inner
        self._egress = egress

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # A DNS name: resolve, validate (rebind guard), and pin.
            pinned = resolve_and_validate(host, self._egress)
            request = self._pin(request, host, pinned)
        return await self._inner.handle_async_request(request)

    @staticmethod
    def _pin(request: httpx.Request, host: str, pinned: _IpAddress) -> httpx.Request:
        """Rewrite *request* to connect to *pinned* while keeping Host + SNI as *host*."""
        # Preserve the original authority for the Host header and TLS SNI.
        if "host" not in (k.lower() for k in request.headers):
            request.headers["Host"] = f"{host}:{request.url.port}" if request.url.port else host
        # ip_address renders IPv6 bare; httpx.URL wants it bracketed in a netloc.
        host_for_url = f"[{pinned}]" if pinned.version == 6 else str(pinned)
        request.url = request.url.copy_with(host=host_for_url)
        request.extensions = {**request.extensions, "sni_hostname": host}
        return request

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_pinned_transport(egress: EgressConfig | None) -> httpx.AsyncBaseTransport | None:
    """Build a DNS-pinning transport for *egress*, or ``None`` if pinning is off.

    Returns ``None`` when no egress policy is supplied or ``dns_pinning_enabled`` is
    ``False`` — callers pass the result straight to ``httpx.AsyncClient(transport=...)``,
    where ``None`` means "use httpx's default transport" (unchanged behaviour). This is
    the one-liner the registry fetchers use so their SSRF guard survives a rebind.
    """
    if egress is None or not egress.dns_pinning_enabled:
        return None
    return DnsPinningTransport(httpx.AsyncHTTPTransport(), egress)
