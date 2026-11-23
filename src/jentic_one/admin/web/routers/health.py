"""Admin health check endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends

from jentic_one.admin.services.health_service import HealthService
from jentic_one.admin.web.deps import get_health_service
from jentic_one.admin.web.schemas.health import HealthResponse

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("/health", summary="Admin health and first-run status")
async def health(svc: HealthService = Depends(get_health_service)) -> HealthResponse:
    """Report admin-surface health, and whether the deployment still needs setup.

    Unlike the dependency-free root `/health` liveness probe, this one reaches the
    database to answer `setup_required`: true while no user exists, which is what
    the SPA and CLI read before offering a login. A database that cannot be
    reached still answers 200 with `setup_required: false`, so treat this as a
    readiness hint rather than a database health check.
    """
    try:
        view = await svc.get_health()
    except Exception:
        logger.debug("health_check_db_unreachable", exc_info=True)
        return HealthResponse(status="ok", surface="admin", setup_required=False)
    return HealthResponse(
        status=view.status,
        surface=view.surface,
        setup_required=view.setup_required,
        next_step=view.next_step,
    )
