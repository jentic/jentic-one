"""Control application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from jentic_one.control.services.access_requests.errors import AccessRequestServiceError
from jentic_one.control.services.credentials.errors import CredentialServiceError
from jentic_one.control.services.toolkits.errors import ToolkitServiceError
from jentic_one.control.web.errors import (
    access_request_service_error_handler,
    credential_service_error_handler,
    database_error_handler,
    toolkit_service_error_handler,
)
from jentic_one.control.web.routers import access_requests, credentials, toolkits
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import (
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from jentic_one.shared.web.app_factory import create_surface_app
from jentic_one.shared.web.health import make_health_router


def get_routers() -> list[tuple[APIRouter, str, list[str]]]:
    """Return routers with (router, prefix, tags) for combined-mode inclusion."""
    # Tags are assigned centrally by the OpenAPI tag resolver (see
    # shared/web/openapi_meta.resolve_tag), so no coarse include-level tags here.
    return [
        (make_health_router("control"), "/control", []),
        (credentials.router, "", []),
        (toolkits.router, "", []),
        (access_requests.router, "", []),
    ]


def get_exception_handlers() -> list[tuple[type[Exception], Any]]:
    """Return surface-specific exception handlers to register on the combined app."""
    return [
        (CredentialServiceError, credential_service_error_handler),
        (ToolkitServiceError, toolkit_service_error_handler),
        (AccessRequestServiceError, access_request_service_error_handler),
        (DatabaseIntegrityError, database_error_handler),
        (DatabaseDataError, database_error_handler),
        (DatabaseUnavailableError, database_error_handler),
    ]


def create_app(ctx: Context) -> FastAPI:
    """Create the control FastAPI application for standalone deployment."""
    app = create_surface_app(ctx, title="jentic-one-control", routers=get_routers())
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    return app
