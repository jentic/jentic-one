"""Fan-out: turning one internal event into N outbound deliveries.

Given an event, find every active ``notification`` endpoint subscribed to its
type and insert one ``webhook_deliveries`` row each. One event becomes N
independent deliveries, so a single broken destination retries on its own
schedule without holding up the others.

Fan-out is **idempotent**, and that is what makes the relay safe. Each delivery
is anchored to a ``webhook_events`` row keyed by ``(endpoint_id,
source_event_id)``, where ``source_event_id`` is the internal event's id. If the
relay processes the same event twice — a crash between sending and advancing its
cursor, say — the unique constraint refuses the second insert and no duplicate
delivery is created. The relay is therefore free to be at-least-once while the
observable behaviour is exactly-once.
"""

from __future__ import annotations

from typing import Any

from jentic_one.admin.repos.webhook_repo import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType

# Event types never sent to an outbound endpoint, whatever an endpoint asks for.
#
# An endpoint with an empty ``event_types`` means "subscribe to everything", so
# without this a catch-all endpoint would stream these off the box. They are
# withheld for two different reasons:
#
# * ``credential.accessed`` is a per-use audit record — high volume, and its
#   pattern alone (which credential, how often, when) is sensitive even though
#   the secret is never included.
# * ``instance.booted`` / ``instance.initialized`` are lifecycle noise nothing
#   downstream can act on.
#
# Deliberately a denylist rather than a filter applied at subscription time: a
# new endpoint created tomorrow inherits the protection automatically.
NEVER_RELAYED: frozenset[str] = frozenset(
    {
        EventType.CREDENTIAL_ACCESSED,
        EventType.INSTANCE_BOOTED,
        EventType.INSTANCE_INITIALIZED,
    }
)


def build_notification_payload(
    *,
    event_id: str,
    event_type: str,
    severity: str,
    summary: str,
    data: dict[str, Any] | None,
    created_at: str | None,
) -> dict[str, Any]:
    """Shape the body a receiver sees for an internal event.

    Only fields a receiver can legitimately act on are included. Notably absent
    are ``detail`` (free-text, may quote upstream responses) and the actor
    columns — a webhook payload leaving the box should not become an
    exfiltration path for internals. Receivers that need more should call the
    API with the event id.
    """
    return {
        "event_id": event_id,
        "event_type": event_type,
        "severity": severity,
        "summary": summary,
        "created_at": created_at,
        "data": data or {},
    }


class WebhookFanoutService:
    """Turns internal events into queued outbound deliveries."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def fan_out(
        self,
        session: Any,
        *,
        source_event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[str]:
        """Queue a delivery per subscribed endpoint; return the delivery ids.

        The caller passes its own ``session`` so fan-out joins the caller's
        transaction — the relay advances its cursor and records the fan-out
        atomically, which is what stops a crash from losing or duplicating work.

        Returns an empty list when the event type is withheld or nothing is
        subscribed; "no subscribers" is a normal outcome, not an error.
        """
        if event_type in NEVER_RELAYED:
            return []

        endpoints = await WebhookEndpointRepository.list_subscribers(session, event_type)
        delivery_ids: list[str] = []
        for endpoint in endpoints:
            if not endpoint.target_url:
                # A notification endpoint with nowhere to send is misconfigured;
                # skip rather than queue a delivery that can only ever fail.
                continue
            event = await WebhookEventRepository.record_event(
                session,
                endpoint_id=endpoint.id,
                source_event_id=source_event_id,
                event_type=event_type,
                payload=payload,
            )
            if event is None:
                # Already fanned out to this endpoint — the dedupe constraint did
                # its job. Not an error, and not a reason to skip the others.
                continue
            delivery = await WebhookDeliveryRepository.enqueue(
                session, event_id=event.id, endpoint_id=endpoint.id
            )
            delivery_ids.append(delivery.id)
        return delivery_ids
