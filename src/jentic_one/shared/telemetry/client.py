"""Dedicated HTTP client for telemetry POSTs.

Deliberately **separate** from the broker's egress/SSRF-guarded client: this
talks only to the configured Jentic ingest endpoint, with its own short timeout.
It is not an OTel exporter, so it lives outside the metrics facade. Every send
swallows + logs failures — telemetry must never affect request flow.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from jentic_one.shared.telemetry.events import TelemetryEvent

logger = structlog.get_logger(__name__)

_PATH = "/jentic-one/telemetry"


def _serialise(event: TelemetryEvent, *, instance_id: str, version: str) -> dict[str, Any]:
    """Build the exact wire payload: ``{id, version, event, actor_type?, tags?, ts}``.

    No other keys are ever produced — this is what makes PII leakage structurally
    impossible. ``tags`` is omitted entirely when there are none (not sent as an
    empty list or null). ``actor_type`` is a closed ``ActorType`` enum member
    (the sink drops any non-member before it ever reaches here); it is omitted
    when None, and any ``_`` is normalised to ``-`` to match the server's enum
    (a no-op for the current ``user`` / ``agent`` members).
    """
    payload: dict[str, Any] = {
        "id": instance_id,
        "version": version,
        "event": str(event.name),
        "ts": event.ts.isoformat(),
    }
    if event.actor_type is not None:
        payload["actor_type"] = event.actor_type.value.replace("_", "-")
    if event.tags:
        payload["tags"] = [str(tag) for tag in event.tags]
    return payload


class TelemetryClient:
    """Thin async HTTP client that POSTs telemetry events one-by-one."""

    def __init__(
        self,
        *,
        endpoint: str,
        instance_id: str,
        version: str,
        request_timeout_s: float,
    ) -> None:
        self._url = endpoint.rstrip("/") + _PATH
        self._instance_id = instance_id
        self._version = version
        self._client = httpx.AsyncClient(timeout=request_timeout_s)

    async def send(self, events: list[TelemetryEvent]) -> None:
        """POST each event. Failures are logged and swallowed, never raised."""
        for event in events:
            payload = _serialise(event, instance_id=self._instance_id, version=self._version)
            try:
                resp = await self._client.post(self._url, json=payload)
                if resp.status_code >= 400:
                    logger.warning(
                        "telemetry_send_rejected",
                        status=resp.status_code,
                        telemetry_event=payload["event"],
                    )
            except Exception:
                logger.warning("telemetry_send_failed", telemetry_event=payload["event"])

    async def aclose(self) -> None:
        await self._client.aclose()
