"""Health response schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response for the admin surface."""

    status: str = Field(description="Health status of the surface; `ok` when it is serving.")
    surface: str = Field(description="Name of the surface reporting health; always `admin`.")
    setup_required: bool = Field(
        description="Whether first-run setup is still needed because no user exists yet."
    )
    next_step: str | None = Field(
        default=None,
        description=(
            "Next setup step to perform, e.g. `create_admin`; null when setup is complete."
        ),
    )
