"""MCP reporting router — config-registration reports from the CLI."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from jentic_one.control.services.mcp.service import McpService
from jentic_one.control.web.deps import get_mcp_service
from jentic_one.control.web.schemas.mcp import (
    McpConfigRegistrationRequest,
    McpConfigRegistrationResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()


@router.post(
    "/mcp/config-registrations",
    status_code=202,
    summary="Report MCP config registration",
)
async def report_mcp_config_registration(
    body: McpConfigRegistrationRequest,
    identity: Identity = get_current_identity(),
    svc: McpService = Depends(get_mcp_service),
) -> McpConfigRegistrationResponse:
    """Record that an MCP server entry was written for an agent runtime.

    Called by `jentic setup` / `jentic skill init` once per runtime whose MCP
    config entry it wrote (best-effort on the CLI side — a failed report never
    blocks setup). Emits the `mcp.config_registered` internal event; when the
    operator opted into telemetry, the event is forwarded carrying at most the
    closed runtime tag. Reports are throttled per (actor, runtime) within a
    24h in-process window — a throttled repeat is still a 202 with
    `recorded: false`, since the CLI treats the report as fire-and-forget.
    Any authenticated actor may report.
    """
    recorded = await svc.report_config_registered(
        runtime=body.runtime,
        actor_id=identity.sub,
        actor_type=identity.actor_type.value,
    )
    return McpConfigRegistrationResponse(runtime=body.runtime, recorded=recorded)
