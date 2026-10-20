"""APIs the broker serves built in, with an API identity but no registry entry.

A credential is scoped to an API identity, and an agent's binding is derived
for one. A built-in API has an identity like any imported API but no catalog
entry and no OpenAPI document: the broker serves its endpoints itself.

A vendor-registry entry can still target one, by naming the built-in API's
catalog api_id as its ``vendor`` (``vendors.entries.<key>.vendor:
github.com/github-git``). The normal connect flow then creates a credential
scoped to exactly that API (the api_id decomposes as any catalog id does:
``github-com`` / ``github-git``), and no catalog import is attempted for it.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

#: The catalog api_id of the GitHub git API (git smart HTTP to github.com,
#: served by the broker's governed git pipeline).
GITHUB_GIT_CATALOG_API_ID = "github.com/github-git"
#: The GitHub git API's identity: the slug of ``github.com`` and the api_id's
#: sub segment, as ``canonical_credential_scope`` derives them, and version 1.
GITHUB_GIT_API_VENDOR = "github-com"
GITHUB_GIT_API_NAME = "github-git"
GITHUB_GIT_API_VERSION = "1"

#: Every built-in API's catalog api_id.
BUILTIN_CATALOG_API_IDS: frozenset[str] = frozenset({GITHUB_GIT_CATALOG_API_ID})


def is_builtin_catalog_api_id(api_id: str) -> bool:
    """Whether ``api_id`` names a built-in API (no catalog entry to import)."""
    return api_id.strip().lower() in BUILTIN_CATALOG_API_IDS


__all__ = [
    "BUILTIN_CATALOG_API_IDS",
    "GITHUB_GIT_API_NAME",
    "GITHUB_GIT_API_VENDOR",
    "GITHUB_GIT_API_VERSION",
    "GITHUB_GIT_CATALOG_API_ID",
    "is_builtin_catalog_api_id",
]
