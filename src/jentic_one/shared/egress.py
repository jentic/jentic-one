"""Shared egress safety: connection-time DNS-rebinding guard + pin.

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
The lookup may reuse a raw resolver answer from the last few seconds
(``shared/dns_cache`` — usually the request's own pre-flight lookup); the answer
is re-validated here every time, so the pinned IP is always one this transport
approved.

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
from jentic_one.shared.dns_cache import resolve_host, resolve_host_blocking
from jentic_one.shared.url_validation import assert_ip_allowed

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UpstreamHostUnresolvedError(ValueError):
    """The upstream host did not resolve, or resolved to no address, at connect time.

    A ``ValueError`` so ``except ValueError`` callers keep failing closed on it;
    the broker's runner maps it to a connect failure rather than a policy refusal.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(f"upstream host did not resolve: {host}")


def resolve_and_validate(host: str, egress: EgressConfig | None) -> _IpAddress:
    """Resolve *host* and return the first IP that passes the egress policy.

    Validates **every** resolved address against ``assert_ip_allowed`` so a
    multi-record answer can't smuggle a blocked IP past the check, then returns
    the first one to pin the connection to. Raises :class:`UpstreamHostUnresolvedError`
    if the host can't be resolved, or ``ValueError`` if any resolved IP is
    blocked (the rebind guard).

    Blocking; :func:`resolve_and_validate_async` is the event-loop variant.
    """
    try:
        addrs = resolve_host_blocking(host)
    except socket.gaierror as exc:
        raise UpstreamHostUnresolvedError(host) from exc
    return _validated_pin(host, addrs, egress)


async def resolve_and_validate_async(host: str, egress: EgressConfig | None) -> _IpAddress:
    """Async :func:`resolve_and_validate`: lookup off the loop, same validation.

    The answer may come from the short-lived raw-answer cache in
    ``shared/dns_cache`` (typically populated by this request's own pre-flight
    check). The cache holds addresses, not verdicts, so every address is
    validated against *egress* here on each call and the pin is always an IP
    this call has just approved.
    """
    try:
        addrs = await resolve_host(host)
    except socket.gaierror as exc:
        raise UpstreamHostUnresolvedError(host) from exc
    return _validated_pin(host, addrs, egress)


def _validated_pin(
    host: str, addrs: tuple[_IpAddress, ...], egress: EgressConfig | None
) -> _IpAddress:
    """Validate every address in *addrs* and return the first (the pin target)."""
    if not addrs:
        raise UpstreamHostUnresolvedError(host)

    for addr in addrs:
        # A DNS name re-resolved here, so pass the hostname for the suffix
        # exemption — matches the validation-time semantics.
        assert_ip_allowed(addr, egress, hostname=host)
    return addrs[0]


class DnsPinningTransport(httpx.AsyncBaseTransport):
    """Wraps a transport to resolve+validate+pin the host IP per request.

    An IP-literal host is validated against the policy and otherwise passed
    through unchanged (nothing to rebind). For a DNS name the request URL host is
    rewritten to the validated IP, the original host is preserved as the ``Host``
    header, and ``sni_hostname`` is set so TLS still validates against the real
    certificate name. A name that does not resolve fails closed.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, egress: EgressConfig | None) -> None:
        self._inner = inner
        self._egress = egress

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            # A DNS name: resolve (off the event loop), validate every address
            # (rebind guard), and pin to the validated IP.
            pinned = await resolve_and_validate_async(host, self._egress)
            request = self._pin(request, host, pinned)
        else:
            # An IP literal: nothing to resolve, but still apply the policy so a
            # caller that skipped pre-flight validation can't reach a blocked IP.
            assert_ip_allowed(literal, self._egress, hostname=None)
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


def build_strict_pinned_transport() -> httpx.AsyncBaseTransport:
    """Build a DNS-pinning transport under the strict default egress policy.

    For outbound calls that have no operator-configurable egress policy (the
    control plane's OAuth token / device-authorization / identity-probe calls):
    every private, loopback, and non-global target is refused at connect time,
    and a host that does not resolve fails closed.
    """
    return DnsPinningTransport(httpx.AsyncHTTPTransport(), None)
