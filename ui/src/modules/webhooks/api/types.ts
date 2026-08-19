/**
 * Webhooks module entity types + mappers.
 *
 * Translates the generated `snake_case` API models into the `camelCase` shapes
 * views consume, so a backend field rename lands here rather than in every
 * component. Mirrors the Agents module's `api/types.ts`.
 *
 * Note what is deliberately absent from every entity: **secret material**. The
 * plaintext secret exists only in the create/rotate *results*
 * (`CreatedEndpoint`, `RotatedSecret`), which are handed straight to the reveal
 * dialog and never cached. The API cannot return a secret from a read, and
 * these types make it impossible for a view to expect one.
 */
import type { WebhookDeliveryResponse, WebhookEndpointResponse } from '@/shared/api';

/**
 * Delivery lifecycle, straight from the backend's status constants.
 *
 * `succeeded` (not "delivered") is the real value — worth stating because the
 * near-synonym is an easy and silent mistake to make when writing a filter.
 */
export type WebhookDeliveryStatus = 'pending' | 'succeeded' | 'failed' | 'dead';

/** A configured endpoint. Carries no secret. */
export interface WebhookEndpointEntity {
	id: string;
	name: string;
	/** Where we POST the signed events. */
	targetUrl: string | null;
	/** Subscribed event types; empty means "every relayable type". */
	eventTypes: string[];
	active: boolean;
	createdAt: string | null;
}

/** One delivery attempt record — the operator's debugging view. */
export interface WebhookDeliveryEntity {
	id: string;
	eventId: string;
	endpointId: string;
	status: WebhookDeliveryStatus;
	attemptCount: number;
	nextAttemptAt: string | null;
	lastAttemptAt: string | null;
	lastStatusCode: number | null;
	lastError: string | null;
	createdAt: string | null;
}

/**
 * A freshly created endpoint plus its one-time secret.
 *
 * The secret is unrecoverable afterwards, so the caller must show it before
 * discarding this object. It is deliberately NOT part of
 * `WebhookEndpointEntity`, which is what gets cached and re-rendered.
 */
export interface CreatedEndpoint {
	endpoint: WebhookEndpointEntity;
	secret: string;
}

/** The result of rotating a secret: the new key, shown once. */
export interface RotatedSecret {
	endpointId: string;
	secret: string;
	/**
	 * When the *previous* secret stops working. `null` means it was revoked
	 * immediately (a `grace_seconds: 0` rotation, for a leak).
	 */
	previousSecretExpiresAt: string | null;
}

export function endpointToEntity(response: WebhookEndpointResponse): WebhookEndpointEntity {
	return {
		id: response.endpoint_id,
		name: response.name,
		targetUrl: response.target_url ?? null,
		eventTypes: response.event_types ?? [],
		active: response.active,
		createdAt: response.created_at ?? null,
	};
}

export function deliveryToEntity(response: WebhookDeliveryResponse): WebhookDeliveryEntity {
	return {
		id: response.delivery_id,
		eventId: response.event_id,
		endpointId: response.endpoint_id,
		status: response.status as WebhookDeliveryStatus,
		attemptCount: response.attempt_count,
		nextAttemptAt: response.next_attempt_at ?? null,
		lastAttemptAt: response.last_attempt_at ?? null,
		lastStatusCode: response.last_status_code ?? null,
		lastError: response.last_error ?? null,
		createdAt: response.created_at ?? null,
	};
}
