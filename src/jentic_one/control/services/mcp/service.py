"""MCP config-registration reporting (local-MCP item 2-E3, issue #1189).

The CLI (`jentic setup` / `jentic skill init`) writes one MCP server entry per
detected agent runtime and then calls the control plane once per runtime
written. This service turns that call into the ``mcp.config_registered``
internal event, which ``emit_event`` also forwards to the telemetry sink (when
the operator opted in) carrying at most the closed ``McpConfigRuntime`` tag —
never a raw runtime string. It is the first step of the config-written →
first-session → first-execute adoption funnel.
"""

from __future__ import annotations

import structlog

from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event
from jentic_one.shared.models.events import EventSeverity, EventType, McpConfigRuntime

logger = structlog.get_logger(__name__)


class McpService:
    """Control-plane service for MCP transport reporting."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def report_config_registered(
        self,
        *,
        runtime: McpConfigRuntime,
        actor_id: str,
        actor_type: str,
    ) -> None:
        """Record that an MCP config entry was written for ``runtime``.

        Opens its own admin-DB transaction (the report is the whole request).
        Deliberately NOT deduped: re-running setup legitimately re-registers
        the entry, and the funnel counts registrations, not distinct runtimes.
        """
        async with self._ctx.admin_db.transaction() as session:
            await emit_event(
                session,
                type=EventType.MCP_CONFIG_REGISTERED,
                severity=EventSeverity.INFO,
                summary=f"MCP config entry registered for {runtime.value}",
                created_by=actor_id,
                actor_id=actor_id,
                actor_type=actor_type,
                data={"runtime": runtime.value},
                tags={runtime},
            )
