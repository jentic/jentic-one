"""Webhook endpoint management router (identity-authenticated).

Manages outbound notification endpoints: destinations we POST signed platform
events to. These routes create and destroy signing authority, so they are
identity-authenticated and require the privileged ``webhooks:write`` scope
(which is not granted to agents by default) for mutations.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Response, status

from jentic_one.admin.core.permissions import WEBHOOKS_READ, WEBHOOKS_WRITE
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.services.webhooks.endpoints import WebhookEndpointService
from jentic_one.admin.web.deps import get_webhook_endpoint_service
from jentic_one.admin.web.schemas.webhooks import (
    WebhookDeliveryListResponse,
    WebhookDeliveryResponse,
    WebhookEndpointCreatedResponse,
    WebhookEndpointCreateRequest,
    WebhookEndpointListResponse,
    WebhookEndpointResponse,
    WebhookEndpointUpdateRequest,
    WebhookSecretRotatedResponse,
    WebhookSecretRotateRequest,
    WebhookTestQueuedResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()


def _endpoint_response(endpoint: WebhookEndpoint) -> WebhookEndpointResponse:
    """Project an endpoint row to its API shape, dropping all secret material.

    Built field-by-field rather than with ``from_orm``/``model_validate`` so that
    adding a column to the model can never silently start publishing it — the
    columns being excluded here are ``secret_encrypted`` and
    ``previous_secret_encrypted``.
    """
    return WebhookEndpointResponse(
        endpoint_id=endpoint.id,
        name=endpoint.name,
        target_url=endpoint.target_url,
        event_types=list(endpoint.event_types or []),
        active=endpoint.active,
        created_at=endpoint.created_at,
    )


@router.post(
    "/webhooks/endpoints",
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook endpoint",
    description=(
        "Creates an outbound notification endpoint and returns the signing secret "
        "**once**. Requires the privileged `webhooks:write` scope, and the creation "
        "is audited."
    ),
)
async def create_endpoint(
    payload: WebhookEndpointCreateRequest,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_WRITE]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointCreatedResponse:
    """Create an endpoint, returning its one-time secret."""
    created = await service.create(
        name=payload.name,
        identity=identity,
        target_url=payload.target_url,
        event_types=payload.event_types,
    )
    return WebhookEndpointCreatedResponse(
        endpoint=_endpoint_response(created.endpoint),
        secret=created.secret,
    )


@router.get(
    "/webhooks/endpoints",
    summary="List webhook endpoints",
)
async def list_endpoints(
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_READ]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointListResponse:
    """List all configured endpoints. Never includes secret material."""
    endpoints = await service.list_all()
    return WebhookEndpointListResponse(data=[_endpoint_response(e) for e in endpoints])


@router.get(
    "/webhooks/endpoints/{endpoint_id}",
    summary="Get a webhook endpoint",
)
async def get_endpoint(
    endpoint_id: str,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_READ]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointResponse:
    """Inspect one endpoint."""
    return _endpoint_response(await service.get(endpoint_id))


@router.patch(
    "/webhooks/endpoints/{endpoint_id}",
    summary="Update a webhook endpoint",
    description=(
        "Partially updates an endpoint's configuration — its name, target URL, "
        "event-type subscription, or active state. Only the fields supplied are "
        "changed. Requires the privileged `webhooks:write` scope, and the change "
        "is audited. **Never** touches or returns the signing secret; rotation is "
        "the separate flow for that. Changing `event_types` affects only future "
        "fan-out, not deliveries already queued."
    ),
)
async def update_endpoint(
    endpoint_id: str,
    payload: WebhookEndpointUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_WRITE]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointResponse:
    """Apply a partial update, returning the endpoint without any secret."""
    # Pass only the fields the caller actually sent so an omitted field is left
    # untouched — sending ``event_types: []`` (subscribe to all) must be
    # distinguishable from omitting it (leave the subscription as-is).
    changes: dict[str, object] = {
        field: getattr(payload, field) for field in payload.model_fields_set
    }
    endpoint = await service.update(endpoint_id, identity=identity, **changes)  # type: ignore[arg-type]
    return _endpoint_response(endpoint)


@router.delete(
    "/webhooks/endpoints/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook endpoint",
)
async def delete_endpoint(
    endpoint_id: str,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_WRITE]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> Response:
    """Delete an endpoint and its delivery history (audited)."""
    await service.delete(endpoint_id, identity=identity)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/webhooks/endpoints/{endpoint_id}:rotate-secret",
    summary="Rotate an endpoint's signing secret",
    description=(
        "Issues a new secret and returns it once. The previous secret keeps working "
        "for a grace period so both sides can be updated without dropping events; "
        "pass `grace_seconds: 0` to revoke it immediately (correct for a leak)."
    ),
)
async def rotate_secret(
    endpoint_id: str,
    payload: WebhookSecretRotateRequest | None = None,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_WRITE]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookSecretRotatedResponse:
    """Rotate the signing secret, returning the new one once."""
    grace = (
        timedelta(seconds=payload.grace_seconds)
        if payload is not None and payload.grace_seconds is not None
        else None
    )
    secret = await service.rotate_secret(endpoint_id, identity=identity, grace=grace)
    endpoint = await service.get(endpoint_id)
    return WebhookSecretRotatedResponse(
        endpoint_id=endpoint_id,
        secret=secret,
        previous_secret_expires_at=endpoint.previous_secret_expires_at,
    )


@router.get(
    "/webhooks/endpoints/{endpoint_id}/deliveries",
    summary="List delivery attempts for an endpoint",
    description=(
        "The delivery log: what was sent, what came back, how many attempts, and "
        "when the next one is due. Dead-lettered rows stay here for inspection "
        "rather than being deleted, so a failure can be diagnosed after the fact."
    ),
)
async def list_deliveries(
    endpoint_id: str,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_READ]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookDeliveryListResponse:
    """Delivery history for one endpoint."""
    deliveries = await service.list_deliveries(endpoint_id)
    return WebhookDeliveryListResponse(
        data=[
            WebhookDeliveryResponse(
                delivery_id=d.id,
                event_id=d.event_id,
                endpoint_id=d.endpoint_id,
                status=d.status,
                attempt_count=d.attempt_count,
                next_attempt_at=d.next_attempt_at,
                last_attempt_at=d.last_attempt_at,
                last_status_code=d.last_status_code,
                last_error=d.last_error,
                created_at=d.created_at,
            )
            for d in deliveries
        ]
    )


@router.post(
    "/webhooks/deliveries/{delivery_id}:resend",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resend a delivery",
    description=(
        "Requeues a delivery for another attempt — including a dead-lettered one, "
        "which is the point: an operator can retry after fixing the receiver. "
        "Requeues rather than sending inline, so the dispatcher stays the only "
        "thing doing outbound network I/O."
    ),
)
async def resend_delivery(
    delivery_id: str,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_WRITE]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> Response:
    """Requeue one delivery (audited)."""
    await service.resend(delivery_id, identity=identity)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/webhooks/endpoints/{endpoint_id}:test",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a test event",
    description=(
        "Queues a synthetic `webhook.test` event so wiring can be confirmed without "
        "waiting for something real to happen. The payload is explicitly marked as a "
        "test so a receiver can avoid acting on it."
    ),
)
async def send_test_event(
    endpoint_id: str,
    identity: Identity = get_current_identity(required_permissions=[WEBHOOKS_WRITE]),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookTestQueuedResponse:
    """Queue a test event to a notification endpoint."""
    delivery_id = await service.send_test_event(endpoint_id, identity=identity)
    return WebhookTestQueuedResponse(delivery_id=delivery_id)
