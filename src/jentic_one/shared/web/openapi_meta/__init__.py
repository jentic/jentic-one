"""Static OpenAPI metadata for the Jentic Control Plane API.

This package is the single source of truth for the document-level metadata that
FastAPI cannot infer from type hints and docstrings. Wire it into a FastAPI app
via :func:`fastapi_metadata_kwargs` and :func:`install_openapi_metadata`.

- :mod:`.content` — the ``info`` block, ``servers`` and the security schemes;
- :mod:`.tags` — the tag catalogue and Redoc ``x-tagGroups``;
- :mod:`.operation_ids` — operationId generation, path-to-tag rules and the
  security-adjusting operationId sets;
- :mod:`.responses` — error-response normalisation and per-operation security;
- :mod:`.examples` — the canonical parameter examples and the example passes;
- :mod:`.install` — document assembly (``FastAPI`` kwargs + ``openapi()`` hook).
"""

from __future__ import annotations

from jentic_one.shared.web.openapi_meta.content import (
    API_DESCRIPTION,
    API_SUMMARY,
    API_TITLE,
    API_VERSION,
    BEARER_SECURITY_SCHEME,
    CONTACT,
    LICENSE_INFO,
    OAUTH2_SECURITY_SCHEME,
    PUBLISHED_SERVERS,
    SERVERS,
    authenticated_security,
)
from jentic_one.shared.web.openapi_meta.install import (
    deployment_servers,
    fastapi_metadata_kwargs,
    install_openapi_metadata,
)
from jentic_one.shared.web.openapi_meta.operation_ids import (
    ALWAYS_ERROR_OPERATION_IDS,
    NON_BEARER_AUTH_OPERATION_IDS,
    PUBLIC_OPERATION_IDS,
    PUBLIC_WITH_CLIENT_AUTH_OPERATION_IDS,
    ROUTER_RESHAPED_422_OPERATION_IDS,
    generate_operation_id,
    resolve_tag,
)
from jentic_one.shared.web.openapi_meta.tags import OPENAPI_TAGS, X_TAG_GROUPS

__all__ = [
    "ALWAYS_ERROR_OPERATION_IDS",
    "API_DESCRIPTION",
    "API_SUMMARY",
    "API_TITLE",
    "API_VERSION",
    "BEARER_SECURITY_SCHEME",
    "CONTACT",
    "LICENSE_INFO",
    "NON_BEARER_AUTH_OPERATION_IDS",
    "OAUTH2_SECURITY_SCHEME",
    "OPENAPI_TAGS",
    "PUBLIC_OPERATION_IDS",
    "PUBLIC_WITH_CLIENT_AUTH_OPERATION_IDS",
    "PUBLISHED_SERVERS",
    "ROUTER_RESHAPED_422_OPERATION_IDS",
    "SERVERS",
    "X_TAG_GROUPS",
    "authenticated_security",
    "deployment_servers",
    "fastapi_metadata_kwargs",
    "generate_operation_id",
    "install_openapi_metadata",
    "resolve_tag",
]
