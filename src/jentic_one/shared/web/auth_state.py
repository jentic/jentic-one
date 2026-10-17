"""The pre-auth shared-state backend every anonymous-door limiter reads.

The OAuth doors (``/authorize``, token, revocation, DCR), the consent-handle
and CSRF stores, and the password-login limiter all live on one
:class:`SharedStateBackend` stashed on ``app.state.auth_state_backend``. Each
surface that needs it calls :func:`install_auth_state_backend` from its
``install_on_app``; the call is idempotent, so a combined app builds exactly one
backend (one Redis client) whatever the surface order. The app lifespans close
it on shutdown via :func:`close_auth_state_backend`.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request

from jentic_one.shared.context import Context
from jentic_one.shared.state import BackendKind, build_state_backend
from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend

logger = structlog.get_logger(__name__)

AUTH_STATE_BACKEND_ATTR = "auth_state_backend"


def install_auth_state_backend(app: FastAPI, ctx: Context) -> SharedStateBackend:
    """Return the app's pre-auth backend, building it from config on first call.

    The backend follows ``broker.resilience.backend``. Building a ``memory``
    backend logs a warning: its buckets and consent handles are per process.
    """
    existing: object = getattr(app.state, AUTH_STATE_BACKEND_ATTR, None)
    if isinstance(existing, SharedStateBackend):
        return existing
    backend_cfg = ctx.config.broker.resilience.backend
    backend = build_state_backend(backend_cfg)
    setattr(app.state, AUTH_STATE_BACKEND_ATTR, backend)
    if backend_cfg.backend is BackendKind.MEMORY:
        # Rate limiters and consent-handle state live per process on this
        # backend, so a multi-worker deployment gets independent buckets and
        # loses the anti-replay guarantee across workers.
        logger.warning(
            "auth_state_backend_memory_selected",
            scope="per_process",
            remedy="configure broker.resilience.backend redis for multi-worker deployments",
        )
    return backend


def get_auth_state_backend(request: Request) -> SharedStateBackend:
    """Return the request app's pre-auth backend, installing a memory fallback if absent.

    The fallback is persisted on ``app.state`` so every later request shares
    its buckets instead of each getting a fresh, never-exhausted one.
    """
    backend: object = getattr(request.app.state, AUTH_STATE_BACKEND_ATTR, None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("auth_state_backend_missing", fallback="memory")
    fallback = MemoryStateBackend()
    setattr(request.app.state, AUTH_STATE_BACKEND_ATTR, fallback)
    return fallback


async def close_auth_state_backend(app: FastAPI) -> None:
    """Close the app's pre-auth backend, if one is installed (lifespan teardown)."""
    backend: object = getattr(app.state, AUTH_STATE_BACKEND_ATTR, None)
    if isinstance(backend, SharedStateBackend):
        try:
            await backend.aclose()
        except Exception:
            logger.warning("auth_state_backend_close_failed", exc_info=True)


__all__ = [
    "AUTH_STATE_BACKEND_ATTR",
    "close_auth_state_backend",
    "get_auth_state_backend",
    "install_auth_state_backend",
]
