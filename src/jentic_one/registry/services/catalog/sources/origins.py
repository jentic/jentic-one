"""Which origins may receive a catalog source's credentials.

A source's auth headers go only to an origin its own configuration names, for
every kind: the URLs the kind's settings name (``credential_urls``) plus the
operator's explicit ``auth.origins``. A URL a listing returns (a spec link in a
manifest or an ``apis.json``) never earns the credential by being listed. A kind
that names no URLs sends its credential nowhere unless ``auth.origins`` says so.

Origins are compared by the host the HTTP client actually connects to
(:func:`~jentic_one.registry.services.catalog.fetch.url_origin`): both the
configured origin and the URL actually sent are parsed by ``httpx``, so an
internationalised name is encoded exactly as it will be on the wire. Two
spellings that the client sends to different hosts never match the same origin.
"""

from __future__ import annotations

from collections.abc import Iterable

from jentic_one.registry.services.catalog.fetch import Origin, url_origin
from jentic_one.registry.services.catalog.sources.registry import resolve_catalog_source_kind
from jentic_one.shared.config import CatalogSourceConfig

__all__ = ["Origin", "credential_origins", "url_origin"]


def credential_origins(cfg: CatalogSourceConfig) -> frozenset[Origin]:
    """The origins ``cfg``'s credentials may be sent to (computed from config only)."""
    kind = resolve_catalog_source_kind(cfg.kind)
    urls: list[str] = list(cfg.auth.origins)
    named = getattr(kind, "credential_urls", None)
    if named is not None:
        listed: Iterable[str] = named(kind.config_model.model_validate(cfg.settings))
        urls.extend(listed)
    return frozenset(o for o in map(url_origin, urls) if o is not None)
