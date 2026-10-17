"""Registry application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from jentic_one.registry.services.catalog.search import validate_catalog_search_config
from jentic_one.registry.services.errors import RegistryServiceError
from jentic_one.registry.web.errors import service_error_handler
from jentic_one.registry.web.routers import (
    apis,
    catalog,
    governed_hosts,
    inspect,
    notes,
    overlays,
    search,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseConsistencyError
from jentic_one.shared.web.app_factory import create_surface_app
from jentic_one.shared.web.health import make_health_router


def get_routers() -> list[tuple[APIRouter, str, list[str]]]:
    """Return routers with (router, prefix, tags) for combined-mode inclusion."""
    # Tags are assigned centrally by the OpenAPI tag resolver (see
    # shared/web/openapi_meta.resolve_tag), so no coarse include-level tags here.
    return [
        (make_health_router("registry", include_in_schema=False), "/registry", []),
        (apis.router, "", []),
        (catalog.router, "", []),
        (governed_hosts.router, "", []),
        (inspect.router, "", []),
        (notes.router, "", []),
        (overlays.router, "", []),
        (search.router, "", []),
    ]


def get_exception_handlers() -> list[tuple[type[Exception], Any]]:
    """Return registry-specific exception handlers for the combined app."""
    return [
        (RegistryServiceError, service_error_handler),
        # Belt-and-braces: catch an accidental async lazy load (MissingGreenlet,
        # mapped by the DB transaction wrapper to DatabaseConsistencyError) so it
        # maps to a known, logged 500 rather than escaping as an opaque unhandled
        # traceback. See #642.
        (DatabaseConsistencyError, service_error_handler),
    ]


def install_on_app(app: FastAPI, ctx: Context) -> None:
    """Combined-mode assembly hook: validate the catalog search configuration.

    ``catalog.search_strategy`` must name a registered catalog search strategy;
    an unknown name raises ``ConfigError`` here, at app assembly, instead of
    every search silently falling back to ``token``. Strategies therefore
    register at import time, before the app is built.
    """
    validate_catalog_search_config(ctx.config)


def create_app(ctx: Context) -> FastAPI:
    """Create the registry FastAPI application for standalone deployment."""
    validate_catalog_search_config(ctx.config)
    app = create_surface_app(
        ctx, title="jentic-one-registry", routers=get_routers(), enabled_apps={"registry"}
    )
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    return app
