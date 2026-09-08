"""FastAPI dependencies for the control web layer."""

from __future__ import annotations

from fastapi import Depends

from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.control.services.credentials.connect_service import ConnectService
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.mcp.service import McpService
from jentic_one.control.services.toolkits.service import ToolkitService
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
