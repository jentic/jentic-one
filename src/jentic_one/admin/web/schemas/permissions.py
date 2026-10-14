"""Permission request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionResponse(BaseModel):
    """A single permission entry from the catalogue."""

    name: str = Field(description="Permission name, e.g. `users:read`.", examples=["users:write"])
    description: str = Field(
        description="Human-readable description of what the permission allows.",
        examples=["Create, update, and disable users"],
    )
    implies: list[str] = Field(
        description="Permissions this one implies when granted.", examples=[["users:read"]]
    )
    grantable_by_caller: bool = Field(
        description=(
            "Whether the caller may grant this scope to an agent. `org:admin` callers may "
            "grant any scope; anyone else only scopes they hold or the default agent scopes, "
            "and never `org:admin` or `agents:write`."
        ),
        examples=[True],
    )


class PermissionListResponse(BaseModel):
    """List of available permissions."""

    data: list[PermissionResponse] = Field(description="The permissions in the catalogue.")


class EffectivePermission(BaseModel):
    """A single effective permission with provenance."""

    name: str = Field(description="Permission name.", examples=["users:read"])
    implied_by: str | None = Field(
        default=None,
        description="Assigned permission that implies this one; null when it is assigned directly.",
        examples=["users:write"],
    )


class Permissions(BaseModel):
    """Structured permissions view: assigned + effective."""

    assigned: list[str] = Field(
        description="Permissions assigned directly to the user.",
        examples=[["users:write", "agents:read"]],
    )
    effective: list[EffectivePermission] = Field(
        description="All permissions the user holds, including those implied by assigned ones."
    )


class SetPermissionsRequest(BaseModel):
    """Request body for setting user permissions."""

    permissions: list[str] = Field(
        max_length=50,
        description="Permissions to assign to the user; replaces the current assigned set.",
        examples=[["users:write", "agents:read"]],
    )
