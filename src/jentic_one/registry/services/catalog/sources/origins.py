"""Which origins may receive a catalog source's credentials.

A source's auth headers go only to an origin its own configuration names, for
every kind: the URLs the kind's settings name (``credential_urls``) plus the
operator's explicit ``auth.origins``. A URL a listing returns (a spec link in a
manifest or an ``apis.json``) never earns the credential by being listed. A kind
that names no URLs sends its credential nowhere unless ``auth.origins`` says so.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from jentic_one.registry.services.catalog.sources.registry import resolve_catalog_source_kind
from jentic_one.shared.config import CatalogSourceConfig

#: ``(scheme, host, port)``, with the scheme's default port filled in and the
#: host in its canonical form (see :func:`canonical_host`).
Origin = tuple[str, str, int]


def canonical_host(host: str) -> str | None:
    """``host`` lower-cased, without one trailing dot, internationalised names in
    their ASCII (punycode) form; ``None`` when it can't be encoded.

    ``b.example.`` and ``b.example`` name the same host, and so do ``bücher.example``
    and ``xn--bcher-kva.example``: both forms must match the same origin.
    """
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        return None
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    return host


def url_origin(url: str) -> Origin | None:
    """The origin of an ``http(s)`` URL; ``None`` for anything else."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = canonical_host(parts.hostname)
    if host is None:
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def credential_origins(cfg: CatalogSourceConfig) -> frozenset[Origin]:
    """The origins ``cfg``'s credentials may be sent to (computed from config only)."""
    kind = resolve_catalog_source_kind(cfg.kind)
    urls: list[str] = list(cfg.auth.origins)
    named = getattr(kind, "credential_urls", None)
    if named is not None:
        listed: Iterable[str] = named(kind.config_model.model_validate(cfg.settings))
        urls.extend(listed)
    return frozenset(o for o in map(url_origin, urls) if o is not None)
