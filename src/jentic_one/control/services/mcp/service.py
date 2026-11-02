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

from jentic_one.shared.cache import RecentKeys
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event
from jentic_one.shared.models.events import EventSeverity, EventType, McpConfigRuntime

logger = structlog.get_logger(__name__)

#: One event per (actor, runtime) per day is plenty for an adoption funnel —
#: re-running setup within the window is acknowledged but not re-recorded.
_REGISTRATION_WINDOW_SECONDS = 24 * 60 * 60
#: Ceiling on remembered (actor, runtime) pairs; past it the oldest pair is
#: forgotten, which at worst admits one extra row for it.
_REGISTRATION_WINDOW_MAX_KEYS = 10_000

#: In-process per-(actor, runtime) throttle. The registration endpoint is
#: unmetered and writes a row per call, so a re-run loop (or a hostile client)
#: could grow the events table unboundedly. A cheap IN-PROCESS window is
#: deliberately chosen over a DB-backed dedupe query: the funnel needs "did this
#: actor register this runtime recently", not exact counts, and losing the
#: window on process restart merely admits one extra row — no correctness
#: impact, no extra query per report. State is process-wide (services are
#: constructed per request).
_registration_throttle: RecentKeys[tuple[str, str]] = RecentKeys(
    ttl_seconds=_REGISTRATION_WINDOW_SECONDS, max_keys=_REGISTRATION_WINDOW_MAX_KEYS
)


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
    ) -> bool:
        """Record that an MCP config entry was written for ``runtime``.

        Opens its own admin-DB transaction (the report is the whole request).
        Throttled per (actor, runtime) through the in-process window above:
        returns ``False`` (and writes nothing) when an identical report was
        already accepted within the window, ``True`` when the event was
        recorded. The route surfaces the flag as ``recorded`` so the CLI's
        best-effort report stays a 2xx either way.
        """
        key = (actor_id, runtime.value)
        if not _registration_throttle.try_accept(key):
            logger.debug(
                "mcp config registration throttled",
                actor_id=actor_id,
                runtime=runtime.value,
            )
            return False
        try:
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
        except Exception:
            # A failed write must not burn the window slot — the CLI's next
            # (best-effort) retry should be able to record for real.
            _registration_throttle.release(key)
            raise
        return True
