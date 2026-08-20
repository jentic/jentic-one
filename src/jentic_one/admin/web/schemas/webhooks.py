"""Request/response schemas for webhook endpoint management.

The important detail here is what is **absent**: no response model exposes
``secret_encrypted``, ``previous_secret_encrypted`` or the plaintext secret. The
secret is returned exactly once, by the create and rotate operations, in their own
dedicated response models — so a secret can never leak through a list or read
endpoint by someone adding a field to a shared model.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WebhookEndpointCreateRequest(BaseModel):
    """Request body for creating a webhook endpoint."""

    name: str = Field(min_length=1, max_length=255)
    target_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Required for notification endpoints: the URL signed events are POSTed to.",
    )
    event_types: list[str] = Field(
        default_factory=list,
        description="Event types to subscribe to. Empty means all relayable types.",
    )


class WebhookEndpointUpdateRequest(BaseModel):
    """Partial update for a webhook endpoint (PATCH semantics).

    Every field is optional: only the fields actually present in the request are
    applied, and an omitted field is left unchanged. Deliberately carries **no**
    secret field — editing configuration must never touch signing authority,
    which is the separate rotation flow. An explicit empty ``event_types`` list is
    meaningful (subscribe to every relayable type), which is why the caller must
    distinguish "omitted" from "empty" via the request's set fields.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    target_url: str | None = Field(
        default=None,
        max_length=2048,
        description="The URL signed events are POSTed to. Must be an http(s) URL.",
    )
    event_types: list[str] | None = Field(
        default=None,
        description="Event types to subscribe to. Empty means all relayable types.",
    )
    active: bool | None = Field(
        default=None,
        description="Whether the endpoint receives deliveries. Set false to pause it.",
    )


class WebhookEndpointResponse(BaseModel):
    """A webhook endpoint. Deliberately carries no secret material."""

    endpoint_id: str
    name: str
    target_url: str | None
    event_types: list[str]
    active: bool
    created_at: datetime | None


class WebhookEndpointCreatedResponse(BaseModel):
    """Creation result, including the one-time secret.

    This is the **only** response that carries the plaintext secret (alongside
    rotation). It is not recoverable afterwards: it is stored encrypted so the
    platform can sign and verify with it, but exposing it again would make every
    read a secret-disclosure endpoint. Lost secret ⇒ rotate.
    """

    endpoint: WebhookEndpointResponse
    secret: str = Field(description="Shown once and never again. Store it now.")


class WebhookEndpointListResponse(BaseModel):
    """All configured webhook endpoints."""

    data: list[WebhookEndpointResponse]


class WebhookSecretRotateRequest(BaseModel):
    """Request body for rotating an endpoint secret."""

    grace_seconds: int | None = Field(
        default=None,
        ge=0,
        le=604_800,
        description=(
            "How long the previous secret keeps working, so the far side can be "
            "updated without dropping in-flight events. 0 revokes immediately — "
            "correct for a leaked secret, at the cost of those events. Omit for the "
            "24 hour default."
        ),
    )


class WebhookSecretRotatedResponse(BaseModel):
    """Rotation result, including the new one-time secret."""

    endpoint_id: str
    secret: str = Field(description="The new secret. Shown once and never again.")
    previous_secret_expires_at: datetime | None


class WebhookDeliveryResponse(BaseModel):
    """One delivery attempt record — the operator's debugging view."""

    delivery_id: str
    event_id: str
    endpoint_id: str
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime | None


class WebhookDeliveryListResponse(BaseModel):
    """Delivery history for an endpoint."""

    data: list[WebhookDeliveryResponse]


class WebhookTestQueuedResponse(BaseModel):
    """Result of queueing a test event."""

    delivery_id: str
    message: str = "Test event queued; the dispatcher will send it shortly."
