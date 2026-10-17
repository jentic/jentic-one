"""Plain types shared by the catalog-source config and the registry catalog.

Kept as ``Literal`` aliases (not enums) in ``shared`` so ``shared/config.py`` can
type the ``catalog.sources`` section without importing the registry.
"""

from __future__ import annotations

from typing import Literal

#: Who may see a source's entries: every catalog reader, holders of the org
#: catalog permission, or a per-source reader ACL.
CatalogVisibility = Literal["public", "org", "restricted"]

#: How far a source is trusted. ``curated`` sources own the ``catalog_api_id``
#: values they list; other sources are namespaced away from them.
CatalogTrust = Literal["curated", "internal", "untrusted"]

#: The default ``git-manifest`` listing: the curated public-apis index.
DEFAULT_PUBLIC_MANIFEST_URL = (
    "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/apis.json"
)

#: The slug of the built-in public-apis source.
DEFAULT_PUBLIC_SOURCE_SLUG = "public"
