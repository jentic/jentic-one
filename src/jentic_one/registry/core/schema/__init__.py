"""Registry module schema package — Alembic import target for model discovery."""

from __future__ import annotations

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.shared.db.base import RegistryBase

__all__ = [
    "Api",
    "ApiRevision",
    "CatalogSnapshot",
    "CatalogUpdateCheck",
    "Note",
    "Operation",
    "OperationURLIndex",
    "Overlay",
    "RegistryBase",
    "SecurityScheme",
    "SecuritySchemeFlow",
    "Server",
    "ServerVariable",
    "SpecFile",
]
