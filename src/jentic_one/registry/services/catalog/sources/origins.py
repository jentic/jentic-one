"""Which origins may receive a catalog source's credentials.

A source's auth headers go only to an origin its own configuration names, for
every kind: the URLs the kind's settings name (``credential_urls``) plus the
operator's explicit ``auth.origins``. A URL a listing returns (a spec link in a
manifest or an ``apis.json``) never earns the credential by being listed. A kind
that names no URLs sends its credential nowhere unless ``auth.origins`` says so.

Origins are compared by the host the HTTP client actually connects to: both the
configured origin and the request URL are parsed by ``httpx``, so an
internationalised name is encoded exactly as it will be on the wire. Two
spellings that the client sends to different hosts never match the same origin.
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from jentic_one.registry.services.catalog.sources.registry import resolve_catalog_source_kind
from jentic_one.shared.config import CatalogSourceConfig

#: ``(scheme, host, port)``, with the scheme's default port filled in and the
#: host in its canonical form (see :func:`canonical_host`).
Origin = tuple[str, str, int]

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_host(raw_host: str) -> str | None:
    """The on-the-wire (ASCII) host ``raw_host`` lower-cased, without one trailing
    dot; ``None`` when it is empty or not ASCII.

    ``raw_host`` is ``httpx.URL(...).raw_host``: internationalised names are
    already in the punycode form the client connects to, so ``b.example.`` and
    ``b.example`` match, and so do ``bücher.example`` and ``xn--bcher-kva.example``.
    """
    if not raw_host.isascii():
        return None
    host = raw_host.lower()
    if host.endswith("."):
        host = host[:-1]
    return host or None


def url_origin(url: str) -> Origin | None:
    """The origin of an ``http(s)`` URL as the HTTP client sees it; ``None`` for
    anything else, or for a URL the client would refuse."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError, TypeError):
        return None
    scheme = parsed.scheme
    if scheme not in _DEFAULT_PORTS:
        return None
    port = parsed.port
    if port is not None and not 0 < port < 65536:
        return None
    try:
        host = canonical_host(parsed.raw_host.decode("ascii"))
    except UnicodeDecodeError:
        return None
    if host is None:
        return None
    return scheme, host, port or _DEFAULT_PORTS[scheme]


def credential_origins(cfg: CatalogSourceConfig) -> frozenset[Origin]:
    """The origins ``cfg``'s credentials may be sent to (computed from config only)."""
    kind = resolve_catalog_source_kind(cfg.kind)
    urls: list[str] = list(cfg.auth.origins)
    named = getattr(kind, "credential_urls", None)
    if named is not None:
        listed: Iterable[str] = named(kind.config_model.model_validate(cfg.settings))
        urls.extend(listed)
    return frozenset(o for o in map(url_origin, urls) if o is not None)
