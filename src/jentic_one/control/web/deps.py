"""FastAPI dependencies for the control web layer."""

from __future__ import annotations

from fastapi import Depends, Request

from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.control.services.credentials.connect_service import ConnectService
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
)
from jentic_one.control.services.mcp.service import McpService
from jentic_one.control.services.toolkits.service import ToolkitService
from jentic_one.control.services.vendors.service import VendorRegistryService
from jentic_one.shared.catalog import CatalogAutoImportProtocol
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_ctx


def get_credential_service(ctx: Context = Depends(get_ctx)) -> CredentialService:
    """Resolve a CredentialService from the request context."""
    return CredentialService(ctx)


def get_connect_service(ctx: Context = Depends(get_ctx)) -> ConnectService:
    """Resolve a ConnectService from the request context."""
    return ConnectService(ctx)


def get_toolkit_service(ctx: Context = Depends(get_ctx)) -> ToolkitService:
    """Resolve a ToolkitService from the request context."""
    return ToolkitService(ctx)


def get_access_request_service(ctx: Context = Depends(get_ctx)) -> AccessRequestService:
    """Resolve an AccessRequestService from the request context."""
    return AccessRequestService(ctx)


def get_mcp_service(ctx: Context = Depends(get_ctx)) -> McpService:
    """Resolve a McpService from the request context."""
    return McpService(ctx)


def get_vendor_registry_service(
    ctx: Context = Depends(get_ctx),
) -> VendorRegistryService:
    """Resolve a VendorRegistryService from the request context."""
    return VendorRegistryService(ctx)


def get_connect_session_service(
    request: Request,
    ctx: Context = Depends(get_ctx),
) -> ConnectSessionService:
    """Resolve a ConnectSessionService from the request context.

    Threads the process-level ``catalog_auto_importer`` (installed by
    ``wiring.install_control_catalog_auto_importer`` when the same process
    also serves the registry) so a successful connect can enqueue an import
    of the vendor's OpenAPI spec — the broker needs it registered before it
    can route requests. When absent, the service silently skips that step.
    """
    importer: CatalogAutoImportProtocol | None = getattr(
        request.app.state, "catalog_auto_importer", None
    )
    return ConnectSessionService(ctx, catalog_auto_importer=importer)
