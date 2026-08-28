"""Request/response schemas for the MCP reporting routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models.events import McpConfigRuntime


class McpConfigRegistrationRequest(BaseModel):
    """Report that an MCP config entry was written for one agent runtime."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"runtime": "cursor"}]})

    runtime: McpConfigRuntime = Field(
        description=(
            "The agent runtime whose MCP config entry was written — a closed set; "
            "clients map anything unrecognised to `other`."
        )
    )


class McpConfigRegistrationResponse(BaseModel):
    """Acknowledgement of a config-registration report."""

    runtime: McpConfigRuntime = Field(description="The runtime the report was recorded for.")
    recorded: bool = Field(description="Whether the report was accepted for recording.")
