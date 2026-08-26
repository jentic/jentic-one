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
import type {
	WebhookDeliveryAttemptResponse,
	WebhookDeliveryResponse,
	WebhookEndpointResponse,
	WebhookEndpointStatsResponse,
} from '@/shared/api';

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
	/**
	 * Per-endpoint IP/CIDR allowlist. Empty means only the operator-wide egress
	 * policy applies. A listed CIDR permits an otherwise-blocked (private) pinned
	 * IP inside it — never the cloud-metadata range, which stays denied.
	 */
	allowedCidrs: string[];
	active: boolean;
	createdAt: string | null;
	/**
	 * When a *previous* signing secret (from a graceful rotation) stops working.
	 * `null` means no rotation grace is in effect. Drives the grace-window badge.
	 */
	previousSecretExpiresAt: string | null;
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
	/** Wall-clock duration of the most recent attempt, in ms; `null` if untimed. */
	durationMs: number | null;
	createdAt: string | null;
}

/**
 * One recorded attempt in a delivery's history — the full per-attempt timeline
 * the parent delivery does not keep (it stores only the last outcome).
 */
export interface WebhookDeliveryAttemptEntity {
	id: string;
	deliveryId: string;
	/** 1-based ordinal within the delivery. */
	attemptNumber: number;
	statusCode: number | null;
	/** Categorised, non-sensitive failure reason; `null` on success. */
	error: string | null;
	durationMs: number | null;
	createdAt: string | null;
}

/**
 * Aggregate delivery health for an endpoint's Overview, all derived from the
 * delivery log — counts by status, last-24h volume/failures, the most recent
 * and next attempts, and the average response time.
 */
export interface WebhookEndpointStats {
	total: number;
	countsByStatus: Record<string, number>;
	recentTotal: number;
	recentFailed: number;
	lastStatusCode: number | null;
	lastAttemptAt: string | null;
	lastDurationMs: number | null;
	nextAttemptAt: string | null;
	avgDurationMs: number | null;
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
		allowedCidrs: response.allowed_cidrs ?? [],
		active: response.active,
		createdAt: response.created_at ?? null,
		previousSecretExpiresAt: response.previous_secret_expires_at ?? null,
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
		durationMs: response.duration_ms ?? null,
		createdAt: response.created_at ?? null,
	};
}

export function attemptToEntity(
	response: WebhookDeliveryAttemptResponse,
): WebhookDeliveryAttemptEntity {
	return {
		id: response.attempt_id,
		deliveryId: response.delivery_id,
		attemptNumber: response.attempt_number,
		statusCode: response.status_code ?? null,
		error: response.error ?? null,
		durationMs: response.duration_ms ?? null,
		createdAt: response.created_at ?? null,
	};
}

export function statsToEntity(response: WebhookEndpointStatsResponse): WebhookEndpointStats {
	return {
		total: response.total,
		countsByStatus: response.counts_by_status ?? {},
		recentTotal: response.recent_total,
		recentFailed: response.recent_failed,
		lastStatusCode: response.last_status_code ?? null,
		lastAttemptAt: response.last_attempt_at ?? null,
		lastDurationMs: response.last_duration_ms ?? null,
		nextAttemptAt: response.next_attempt_at ?? null,
		avgDurationMs: response.avg_duration_ms ?? null,
	};
}
