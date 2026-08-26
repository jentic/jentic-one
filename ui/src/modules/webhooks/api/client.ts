/**
 * Webhooks repository tier.
 *
 * The ONLY place in the Webhooks module that talks to `@/shared/api`. Views and
 * hooks never import the facade directly — ESLint enforces this. Thin wrappers
 * that turn typed service calls into UI entities and normalise errors into a
 * single sentinel, mirroring the backend's Repository layer.
 *
 * Response-code contract (verified against the real backend on :8100):
 *   POST   /webhooks/endpoints                → 201 + endpoint + one-time secret
 *   GET    /webhooks/endpoints[/{id}]         → 200
 *   PATCH  /webhooks/endpoints/{id}           → 200 + endpoint (never a secret)
 *   DELETE /webhooks/endpoints/{id}           → 204 no body (callers refetch)
 *   GET    /webhooks/endpoints/{id}/deliveries → 200
 *   GET    /webhooks/endpoints/{id}/stats      → 200 (aggregate delivery health)
 *   GET    /webhooks/deliveries/{id}/attempts  → 200 (per-attempt history)
 *   GET    /webhooks/event-catalog             → 200 (subscribable event types)
 *   POST   .../{id}:rotate-secret             → 200 + the new secret, once
 *   POST   .../{id}:test                      → 202 + delivery id
 *   POST   /webhooks/deliveries/{id}:resend   → 202 no body
 */
import { ApiError, WebhooksService } from '@/shared/api';
import {
	attemptToEntity,
	deliveryToEntity,
	endpointToEntity,
	statsToEntity,
	type CreatedEndpoint,
	type RotatedSecret,
	type WebhookDeliveryAttemptEntity,
	type WebhookDeliveryEntity,
	type WebhookEndpointEntity,
	type WebhookEndpointStats,
} from '@/modules/webhooks/api/types';

/**
 * Sentinel error for Webhooks repository calls. Hooks/components branch on
 * `error instanceof WebhooksApiError` without importing the generated
 * `ApiError`. `status` is null for network/parse failures that never reached
 * the server.
 */
export class WebhooksApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'WebhooksApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toWebhooksError(error: unknown, fallback: string): WebhooksApiError {
	if (error instanceof ApiError) {
		const body = error.body as { detail?: unknown } | undefined;
		let detail: string | undefined;
		if (typeof body?.detail === 'string') {
			detail = body.detail;
		} else if (Array.isArray(body?.detail)) {
			// FastAPI 422 validation error: [{ loc, msg, ... }]
			detail = body.detail
				.map((d) => (d as { msg?: string }).msg)
				.filter(Boolean)
				.join('; ');
		}
		return new WebhooksApiError(detail || error.message || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new WebhooksApiError(error.message || fallback, null, error);
	}
	return new WebhooksApiError(fallback, null, error);
}

export async function listEndpoints(): Promise<WebhookEndpointEntity[]> {
	try {
		const res = await WebhooksService.listEndpoints();
		return res.data.map(endpointToEntity);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to load webhook endpoints.');
	}
}

/**
 * Fetch a single endpoint by id — the detail page's own source of truth (a 404
 * surfaces as a `WebhooksApiError` with `status: 404`, which the page renders as
 * a not-found state rather than a load failure, mirroring the toolkit/agent
 * consoles).
 */
export async function getEndpoint(endpointId: string): Promise<WebhookEndpointEntity> {
	try {
		const res = await WebhooksService.getEndpoint({ endpointId });
		return endpointToEntity(res);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to load the webhook endpoint.');
	}
}

export interface CreateEndpointParams {
	name: string;
	targetUrl?: string | null;
	eventTypes?: string[];
	/** Per-endpoint IP/CIDR allowlist. Empty/omitted = operator egress policy only. */
	allowedCidrs?: string[];
}

/**
 * Create an endpoint. The response carries the plaintext secret **once** — the
 * caller must surface it immediately, because no later read can return it.
 */
export async function createEndpoint(params: CreateEndpointParams): Promise<CreatedEndpoint> {
	try {
		const res = await WebhooksService.createEndpoint({
			requestBody: {
				name: params.name,
				target_url: params.targetUrl?.trim() ? params.targetUrl.trim() : null,
				event_types: params.eventTypes ?? [],
				allowed_cidrs: params.allowedCidrs ?? [],
			},
		});
		return { endpoint: endpointToEntity(res.endpoint), secret: res.secret };
	} catch (error) {
		throw toWebhooksError(error, 'Failed to create the webhook endpoint.');
	}
}

/**
 * Fields an edit may change. All optional so this stays a partial update; the
 * backend leaves any omitted field untouched. Deliberately has no secret field —
 * editing configuration never touches signing authority (that is rotation).
 */
export interface UpdateEndpointParams {
	name?: string;
	targetUrl?: string | null;
	eventTypes?: string[];
	allowedCidrs?: string[];
	active?: boolean;
}

/**
 * Update an endpoint's configuration. Returns the endpoint in its post-edit
 * shape (never a secret). Only the fields present on `params` are sent, so an
 * omitted field is left as-is — and an empty `eventTypes`/`allowedCidrs` array
 * is a real value (subscribe to every relayable type / clear the allowlist),
 * distinct from omitting it.
 */
export async function updateEndpoint(
	endpointId: string,
	params: UpdateEndpointParams,
): Promise<WebhookEndpointEntity> {
	const requestBody: {
		name?: string;
		target_url?: string | null;
		event_types?: string[];
		allowed_cidrs?: string[];
		active?: boolean;
	} = {};
	if (params.name !== undefined) requestBody.name = params.name;
	if (params.targetUrl !== undefined) {
		requestBody.target_url = params.targetUrl?.trim() ? params.targetUrl.trim() : null;
	}
	if (params.eventTypes !== undefined) requestBody.event_types = params.eventTypes;
	if (params.allowedCidrs !== undefined) requestBody.allowed_cidrs = params.allowedCidrs;
	if (params.active !== undefined) requestBody.active = params.active;

	try {
		const res = await WebhooksService.updateEndpoint({ endpointId, requestBody });
		return endpointToEntity(res);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to update the webhook endpoint.');
	}
}

export async function deleteEndpoint(endpointId: string): Promise<void> {
	try {
		await WebhooksService.deleteEndpoint({ endpointId });
	} catch (error) {
		throw toWebhooksError(error, 'Failed to delete the webhook endpoint.');
	}
}

export async function listDeliveries(endpointId: string): Promise<WebhookDeliveryEntity[]> {
	try {
		const res = await WebhooksService.listDeliveries({ endpointId });
		return res.data.map(deliveryToEntity);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to load the delivery log.');
	}
}

/** Aggregate delivery health for an endpoint's Overview (counts, last-24h, timings). */
export async function getEndpointStats(endpointId: string): Promise<WebhookEndpointStats> {
	try {
		const res = await WebhooksService.getEndpointStats({ endpointId });
		return statsToEntity(res);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to load endpoint statistics.');
	}
}

/** The per-attempt history for one delivery (newest first). */
export async function listDeliveryAttempts(
	deliveryId: string,
): Promise<WebhookDeliveryAttemptEntity[]> {
	try {
		const res = await WebhooksService.listDeliveryAttempts({ deliveryId });
		return res.data.map(attemptToEntity);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to load the attempt history.');
	}
}

/**
 * The subscribable event catalog, served by the backend so the picker cannot
 * drift from `EventType.ALL` minus the never-relayed set. Returns the raw event
 * type strings; the module's curated catalog supplies the human copy.
 */
export async function getEventCatalog(): Promise<string[]> {
	try {
		const res = await WebhooksService.getEventCatalog();
		return res.data.map((e) => e.event_type);
	} catch (error) {
		throw toWebhooksError(error, 'Failed to load the event catalog.');
	}
}

/**
 * Rotate the signing secret.
 *
 * `graceSeconds` is how long the *previous* secret keeps working, so the two
 * sides can be updated independently instead of simultaneously. `0` revokes it
 * at once — correct for a leak, at the cost of events already in flight.
 * `undefined` takes the backend's 24-hour default.
 */
export async function rotateSecret(
	endpointId: string,
	graceSeconds?: number,
): Promise<RotatedSecret> {
	try {
		const res = await WebhooksService.rotateSecret({
			endpointId,
			requestBody: graceSeconds === undefined ? null : { grace_seconds: graceSeconds },
		});
		return {
			endpointId: res.endpoint_id,
			secret: res.secret,
			previousSecretExpiresAt: res.previous_secret_expires_at ?? null,
		};
	} catch (error) {
		throw toWebhooksError(error, 'Failed to rotate the signing secret.');
	}
}

/** Queue a synthetic `webhook.test` event. Returns the new delivery's id. */
export async function sendTestEvent(endpointId: string): Promise<string> {
	try {
		const res = await WebhooksService.sendTestEvent({ endpointId });
		return res.delivery_id;
	} catch (error) {
		throw toWebhooksError(error, 'Failed to queue a test event.');
	}
}

/** Requeue a delivery — including a dead-lettered one, which is the point. */
export async function resendDelivery(deliveryId: string): Promise<void> {
	try {
		await WebhooksService.resendDelivery({ deliveryId });
	} catch (error) {
		throw toWebhooksError(error, 'Failed to resend the delivery.');
	}
}
