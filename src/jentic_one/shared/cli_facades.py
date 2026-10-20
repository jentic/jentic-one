"""Vendor CLI facades: which ones the broker serves, and where agents reach them.

Shared by the broker (which serves the facades) and the control surface (which
tells agents' CLI wrappers the address to point each vendor CLI at), so the
two can never disagree on a path-mode prefix.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal

#: Something a vendor's tools reach through Jentic.
FacadeProtocol = Literal["rest", "graphql", "git"]

#: The reserved path prefix every path-mode facade is served under. Its first
#: segment starts with an underscore, which no DNS hostname can contain, so it
#: never shadows a URL the forward-proxy catch-all could otherwise serve.
FACADE_PATH_PREFIX = "/_facade"

#: Vendors whose facade the broker implements, mapped to the vendor CLI it
#: serves. A vendor enabled in ``cli_facades`` but missing here has no facade
#: route yet, so it is never advertised.
SERVED_FACADE_CLIS: MappingProxyType[str, str] = MappingProxyType(
    {"github": "gh", "cloudflare": "cf"}
)


#: What each served facade's vendor CLI reaches through Jentic, besides the
#: facade itself: its REST API, GitHub's GraphQL endpoint (with its own
#: top-level-field rules) and, when the governed git pipeline is on
#: (``cli_facades.git``), git smart HTTP to github.com.
FACADE_PROTOCOLS: MappingProxyType[str, tuple[FacadeProtocol, ...]] = MappingProxyType(
    {"github": ("rest", "graphql", "git"), "cloudflare": ("rest",)}
)

#: The protocol served only while ``cli_facades.git`` is on.
GIT_PROTOCOL: FacadeProtocol = "git"


def facade_path_prefix(vendor: str) -> str:
    """The path prefix ``vendor``'s path-mode facade is served under."""
    return f"{FACADE_PATH_PREFIX}/{vendor}"


__all__ = [
    "FACADE_PATH_PREFIX",
    "FACADE_PROTOCOLS",
    "GIT_PROTOCOL",
    "SERVED_FACADE_CLIS",
    "FacadeProtocol",
    "facade_path_prefix",
]
