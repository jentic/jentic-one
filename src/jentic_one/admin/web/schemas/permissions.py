"""Permission request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionResponse(BaseModel):
    """A single permission entry from the catalogue."""

    name: str
    description: str
    implies: list[str]
    grantable_by_caller: bool = Field(
        description=(
            "Whether the caller may grant this scope to an agent. `org:admin` callers may "
            "grant any scope; anyone else only scopes they hold or the default agent scopes, "
            "and never `org:admin` or `agents:write`."
        )
    )


class PermissionListResponse(BaseModel):
    """List of available permissions."""

    data: list[PermissionResponse]


class EffectivePermission(BaseModel):
    """A single effective permission with provenance."""

    name: str
    implied_by: str | None = None


class Permissions(BaseModel):
    """Structured permissions view: assigned + effective."""

    assigned: list[str]
    effective: list[EffectivePermission]


class SetPermissionsRequest(BaseModel):
    """Request body for setting user permissions."""

    permissions: list[str] = Field(max_length=50)
