"""Auth application factory."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, FastAPI

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import (
    cursor_error_handler,
    database_error_handler,
    service_error_handler,
)
from jentic_one.auth.web.routers import (
    agents,
    authorize,
    discovery,
    identity,
    local_login,
    oauth,
    oauth_client_registration,
    oauth_grants,
    registration,
)

# Re-exported: assemblers outside this package (an overlay's combined app)
# import the control-plane chain from the auth surface's app module.
from jentic_one.auth.web.verifier import make_superset_verifier as make_superset_verifier
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.state import build_state_backend
from jentic_one.shared.state.factory import BackendKind
from jentic_one.shared.web.app_factory import create_surface_app
from jentic_one.shared.web.container import AppContainer
from jentic_one.shared.web.health import make_health_router

logger = structlog.get_logger(__name__)


def get_routers() -> list[tuple[APIRouter, str, list[str]]]:
    """Return routers with (router, prefix, tags) for combined-mode inclusion."""
    # Tags are assigned centrally by the OpenAPI tag resolver (see
    # shared/web/openapi_meta.resolve_tag), so no coarse include-level tags here.
    return [
        (make_health_router("auth", include_in_schema=False), "/auth", []),
        (discovery.router, "", []),
        # /mcp-scoped OAuth discovery: RFC 8414 + RFC 9728 docs,
        # the root protected-resource alias, and the /mcp 401 challenge. All
        # gated by server.mcp.oauth.enabled (404 when off).
        (discovery.mcp_router, "", []),
        (authorize.router, "", []),
        # Local-account login form on the /authorize flow. Gated by
        # auth.local_login.enabled (404 when off).
        (local_login.router, "", []),
        (identity.router, "", []),
        (agents.router, "", []),
        (oauth.router, "", []),
        (oauth_client_registration.router, "", []),
        (oauth_grants.router, "", []),
        (registration.router, "", []),
    ]


def get_exception_handlers() -> list[tuple[type[Exception], Any]]:
    """Return auth-specific exception handlers for registration on the combined app."""
    return [
        (AuthServiceError, service_error_handler),
        (DatabaseUnavailableError, database_error_handler),
        # A user-supplied `?cursor=` that fails to decode must 400, not 500 —
        # the per-agent grant listing decodes it (see auth/web/errors.py).
        (InvalidCursorError, cursor_error_handler),
    ]


def install_on_app(app: FastAPI, ctx: Context) -> None:
    """Install the auth surface's shared state backend on the app.

    The identity verifier is not installed here: ``shared.web.app_factory`` owns
    it (``install_platform_token_verifier``), so standalone and combined apps
    get the same chain and an injected installer's replacement is never
    clobbered by a later surface hook.
    """
    backend_cfg = ctx.config.broker.resilience.backend
    backend = build_state_backend(backend_cfg)
    app.state.auth_state_backend = backend
    if backend_cfg.backend is BackendKind.MEMORY:
        # Rate limiters and consent-handle state live per-process on this
        # backend, so a multi-worker deployment gets independent buckets and
        # loses the anti-replay guarantee across workers. Warn loudly so an
        # operator running >1 worker knows to configure Redis.
        logger.warning(
            "auth_state_backend_memory_selected — rate limits and consent "
            "handles are per-process; configure redis for multi-worker deployments",
        )

    async def _close_auth_backend() -> None:
        await backend.aclose()

    app.router.add_event_handler("shutdown", _close_auth_backend)


def create_app(ctx: Context, container: AppContainer | None = None) -> FastAPI:
    """Create the auth FastAPI application for standalone deployment.

    ``container`` lets the composition root ride its extras (notably the
    ``/mcp`` discovery challenge placeholder on auth-sans-control shapes) on a
    standalone auth process; ``None`` keeps the default wiring.
    """
    app = create_surface_app(
        ctx,
        title="jentic-one-auth",
        routers=get_routers(),
        enabled_apps={"auth"},
        container=container,
    )
    install_on_app(app, ctx)
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    return app
