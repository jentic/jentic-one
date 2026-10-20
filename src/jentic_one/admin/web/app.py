"""Admin application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from jentic_one.admin.services.errors import AdminServiceError
from jentic_one.admin.web.errors import database_error_handler, service_error_handler
from jentic_one.admin.web.routers import (
    actors,
    audit,
    auth,
    config,
    events,
    executions,
    health,
    held_executions,
    jobs,
    monitoring,
    oauth_clients,
    oauth_grants,
    permissions,
    users,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError, DatabaseUnavailableError
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web.app_factory import (
    create_surface_app,
    get_control_plane_reference_router,
)
from jentic_one.shared.web.auth_state import install_auth_state_backend
from jentic_one.shared.web.static import mount_spa


def get_routers() -> list[tuple[APIRouter, str, list[str]]]:
    """Return routers with (router, prefix, tags) for combined-mode inclusion.

    Only ``/health`` keeps a surface-specific prefix in combined mode so it can
    coexist with the other surfaces' health endpoints (and the combined app's
    own root ``/health``). Every other admin route is mounted at the root, so
    the wire contract is identical between ``make start-admin`` (standalone)
    and ``make start-app`` (combined): clients hit ``/users``, ``/auth/login``,
    ``/jobs`` etc. either way.
    """
    # Tags are assigned centrally by the OpenAPI tag resolver (see
    # shared/web/openapi_meta.resolve_tag), so no coarse include-level tags here.
    return [
        (health.router, "/admin", []),
        (actors.router, "", []),
        (auth.router, "", []),
        (users.router, "", []),
        (permissions.router, "", []),
        (held_executions.router, "", []),
        (executions.router, "", []),
        (jobs.router, "", []),
        (events.router, "", []),
        (audit.router, "", []),
        (monitoring.router, "", []),
        (config.router, "", []),
        (oauth_clients.router, "", []),
        (oauth_grants.router, "", []),
    ]


def get_exception_handlers() -> list[tuple[type[Exception], Any]]:
    """Return surface-specific exception handlers to register on the combined app.

    The shared ``ProblemDetailException`` handler is registered centrally by the
    app factory, so only admin-specific handlers are returned here.
    """
    return [
        (AdminServiceError, service_error_handler),
        (InvalidCursorError, service_error_handler),
        (DatabaseIntegrityError, database_error_handler),
        (DatabaseUnavailableError, database_error_handler),
    ]


def install_on_app(app: FastAPI, ctx: Context) -> None:
    """Install the pre-auth state backend ``POST /auth/login``'s rate limiter reads.

    It is shared with the auth surface when both ride one app (built once,
    whatever the order) and closed by the app lifespan. The identity verifier
    is not installed here: ``shared.web.app_factory`` owns it
    (``install_platform_token_verifier``).
    """
    install_auth_state_backend(app, ctx)


def create_app(ctx: Context) -> FastAPI:
    """Create the admin FastAPI application for standalone deployment."""
    app = create_surface_app(
        ctx, title="jentic-one-admin", routers=get_routers(), enabled_apps={"admin"}
    )
    install_on_app(app, ctx)
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    # The docs SPA is served from this surface, so its endpoint reference is
    # too (the split-deploy gateway routes /reference here). A combined app
    # mounts its own and never calls this factory. The reference documents the
    # whole control plane, not just admin's routes.
    app.include_router(get_control_plane_reference_router(ctx))
    # Serve the SPA after all API routers are registered. ``app.frontend()``
    # routes are low-priority, so API routes always win; this only needs the
    # routers present so the bundle never answers a real API path. No-op when no
    # UI bundle is packaged (API-only). Standalone drops the surface prefix, so
    # admin health is served at /health.
    mount_spa(app, health_path="/health")
    return app


def mount_ui(app: FastAPI) -> None:
    """Mount the admin SPA on a combined app.

    Called by the combined app factory after all surfaces' routers are
    registered. ``app.frontend()`` routes are low-priority so they never shadow
    an API route. No-op when no UI bundle is packaged. In combined mode the admin
    health router keeps its ``/admin`` prefix (so surfaces don't collide), so the
    SPA must call ``/admin/health``.
    """
    mount_spa(app, health_path="/admin/health")
