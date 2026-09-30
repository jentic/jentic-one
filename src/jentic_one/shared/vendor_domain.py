"""Derive an API vendor from a hostname via the Public Suffix List.

A vendor is the **registrable domain** (eTLD+1) of an API's host:
``api.stripe.com`` → ``stripe.com``, ``api.finage.co.uk`` → ``finage.co.uk``,
``acme.github.io`` → ``acme.github.io``. The public suffix is resolved against
the full Public Suffix List, **including its PRIVATE section** (``github.io``,
``herokuapp.com``, ``azurewebsites.net``, …), so distinct tenants of a shared
hosting suffix never collapse into one vendor.

A few PRIVATE-section entries are not shared hosting but one operator's own API
namespace (``googleapis.com``: every ``*.googleapis.com`` API host is Google's).
Those are listed in :data:`_SINGLE_OPERATOR_SUFFIXES` and resolve to the suffix
itself, so ``blogger.googleapis.com`` and ``googleapis.com/blogger`` share the
``googleapis.com`` vendor instead of splitting Google across per-product vendors.

The list is the snapshot bundled with the ``publicsuffixlist`` package — it is
read from the installed wheel and never fetched over the network. Refreshing it
is a dependency bump.

This module is the single place a vendor is derived from a hostname; every
caller (catalog manifest, vendor-registry connect, access guidance) routes
through :func:`vendor_from_api_id` / :func:`registrable_domain` so the same host
always yields the same vendor.
"""

from __future__ import annotations

import ipaddress
from functools import cache

from publicsuffixlist import PublicSuffixList

# PRIVATE-section suffixes owned and operated by a single API vendor. A host under
# one of these resolves to the suffix itself rather than to a per-subdomain vendor.
# Keep this list short and limited to operator-owned API namespaces; genuinely
# multi-tenant hosting suffixes (``github.io``, ``herokuapp.com``, ...) must never
# be added here, since that would merge unrelated tenants into one vendor.
_SINGLE_OPERATOR_SUFFIXES: frozenset[str] = frozenset({"googleapis.com"})


@cache
def _psl() -> PublicSuffixList:
    # ``only_icann=False`` keeps the PRIVATE section; ``accept_unknown=True``
    # treats an unlisted TLD (``localhost``, ``internal``) as a public suffix,
    # matching the PSL's implicit ``*`` rule.
    return PublicSuffixList(only_icann=False, accept_unknown=True)


def _normalise_host(host: str) -> str:
    """Lowercase, trim, and drop a trailing root dot and any ``:port``."""
    value = host.strip().lower()
    if value.startswith("["):
        # Bracketed IPv6 literal, optionally with a port: ``[::1]:8080``.
        end = value.find("]")
        return value[1:end] if end != -1 else value[1:]
    if value.count(":") == 1:
        name, _, port = value.partition(":")
        if port.isdigit() or not port:
            value = name
    return value.rstrip(".")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def registrable_domain(host: str) -> str | None:
    """Return the registrable domain (eTLD+1) for *host*, or ``None`` if empty.

    - ``api.stripe.com`` → ``stripe.com``; ``api.finage.co.uk`` → ``finage.co.uk``
    - hosts under a PRIVATE-section suffix keep their tenant label:
      ``acme.herokuapp.com`` → ``acme.herokuapp.com``
    - IP literals are returned as-is (``10.0.0.1``, ``::1``)
    - single-label hosts are returned as-is (``localhost``, ``stripe``)
    - a host that is itself a public suffix (``co.uk``, ``github.io``) has no
      registrable part and is returned as-is rather than being widened further
    - hosts under a single-operator suffix resolve to that suffix:
      ``blogger.googleapis.com`` → ``googleapis.com``
    """
    value = _normalise_host(host)
    if not value:
        return None
    if _is_ip_literal(value):
        return value
    labels = [label for label in value.split(".") if label]
    if not labels:
        return None
    normalised = ".".join(labels)
    if len(labels) == 1:
        return normalised
    for suffix in _SINGLE_OPERATOR_SUFFIXES:
        if normalised == suffix or normalised.endswith(f".{suffix}"):
            return suffix
    # ``privatesuffix`` is ``None`` when the host is itself a public suffix.
    private: str | None = _psl().privatesuffix(normalised)
    return private or normalised


def vendor_from_api_id(api_id: str) -> str | None:
    """Reduce an ``api_id`` (``{host}`` or ``{host}/{sub}``) to its vendor.

    ``api.stripe.com`` → ``stripe.com``; ``slack.com/api`` → ``slack.com``;
    ``github.com/api.github.com`` → ``github.com``; a bare ``stripe`` stays
    ``stripe``. Only the portion before the first ``/`` is considered.
    """
    if not api_id:
        return None
    return registrable_domain(api_id.split("/", 1)[0])
