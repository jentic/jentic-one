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
    allowed_cidrs: list[str] = Field(
        default_factory=list,
        description=(
            "Optional per-endpoint IP/CIDR allowlist. When non-empty, an otherwise-"
            "blocked (private/internal) pinned IP inside one of these CIDRs is "
            "permitted at send — best for stable internal targets. The cloud-"
            "metadata range is never re-opened, whatever is listed. Empty means "
            "only the operator-wide egress policy applies."
        ),
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
    allowed_cidrs: list[str] | None = Field(
        default=None,
        description=(
            "Per-endpoint IP/CIDR allowlist. Omit to leave unchanged; send an "
            "empty list to clear it. Never re-opens the cloud-metadata hard-deny."
        ),
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
    allowed_cidrs: list[str]
    active: bool
    created_at: datetime | None
    # Exposes the rotation grace window so the UI can show a "previous secret
    # still valid until …" badge. Null when no rotation grace is in effect.
    previous_secret_expires_at: datetime | None = None


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
    # A **categorized** failure reason (e.g. ``connection_timeout``,
    # ``http_error_500``, ``endpoint_gone_deactivated``), never a raw exception
    # string — the dispatcher deliberately keeps host/IP/URL detail in
    # server-side logs only, so this is safe to return to the API and show in the
    # UI. See ``WebhookDeliveryDispatcher._categorize_error``.
    last_error: str | None
    duration_ms: int | None = Field(
        default=None,
        description="Wall-clock duration of the most recent attempt, in milliseconds.",
    )
    created_at: datetime | None


class WebhookDeliveryListResponse(BaseModel):
    """Delivery history for an endpoint."""

    data: list[WebhookDeliveryResponse]


class WebhookDeliveryAttemptResponse(BaseModel):
    """One recorded attempt in a delivery's history."""

    attempt_id: str
    delivery_id: str
    attempt_number: int
    status_code: int | None
    error: str | None
    duration_ms: int | None
    created_at: datetime | None


class WebhookDeliveryAttemptListResponse(BaseModel):
    """Per-attempt history for one delivery (newest first)."""

    data: list[WebhookDeliveryAttemptResponse]


class WebhookEndpointStatsResponse(BaseModel):
    """Aggregate delivery health for an endpoint's Overview.

    All derived from ``webhook_deliveries`` — counts by status, last-24h volume
    and failures, the most recent attempt, the next scheduled attempt, and the
    average response time.
    """

    total: int
    counts_by_status: dict[str, int]
    recent_total: int
    recent_failed: int
    last_status_code: int | None
    last_attempt_at: datetime | None
    last_duration_ms: int | None
    next_attempt_at: datetime | None
    avg_duration_ms: float | None


class WebhookEventCatalogEntry(BaseModel):
    """One subscribable event type in the catalog."""

    event_type: str
    noun: str = Field(description="The grouping prefix (before the first '.').")


class WebhookEventCatalogResponse(BaseModel):
    """The canonical set of subscribable event types.

    Served so the UI's event picker cannot drift from the backend
    ``EventType.ALL`` minus the never-relayed set. The synthetic ``webhook.test``
    type is deliberately excluded — it is not subscribable.
    """

    data: list[WebhookEventCatalogEntry]


class WebhookTestQueuedResponse(BaseModel):
    """Result of queueing a test event."""

    delivery_id: str
    message: str = "Test event queued; the dispatcher will send it shortly."
