"""Shared health-check router factory for surfaces without bespoke health logic."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from jentic_one import __version__


class RootHealthResponse(BaseModel):
    """Liveness body of the canonical root ``GET /health`` probe."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "ok", "version": "1.0.0"}]}
    )

    status: str = Field(
        description="`ok` while the process is serving requests. A metered "
        "marketplace deployment whose entitlement lapsed answers `not_entitled` "
        "(still HTTP 200, so orchestrators don't restart it)."
    )
    version: str | None = Field(
        default=None,
        description="Running Jentic One release. Absent on the `not_entitled` body.",
    )
    reason: str | None = Field(
        default=None,
        description="Why the deployment is not entitled. Present only on the `not_entitled` body.",
    )


def make_health_router(surface: str) -> APIRouter:
    """Build a minimal ``/health`` router reporting the given surface name."""
    router = APIRouter()

    @router.get(
        "/health",
        operation_id=f"{surface}Health",
        summary=f"{surface.capitalize()} health",
        tags=["System"],
    )
    async def health() -> dict[str, str]:
        """Return service health status for this surface.

        Unauthenticated liveness probe. In combined mode this is served under
        the surface prefix (e.g. ``/control/health``) so surfaces don't collide;
        the canonical platform probe is the root ``GET /health``.
        """
        return {"status": "ok", "surface": surface, "version": __version__}

    return router
