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
 *   POST   .../{id}:rotate-secret             → 200 + the new secret, once
 *   POST   .../{id}:test                      → 202 + delivery id
 *   POST   /webhooks/deliveries/{id}:resend   → 202 no body
 */
import { ApiError, WebhooksService } from '@/shared/api';
import {
	deliveryToEntity,
	endpointToEntity,
	type CreatedEndpoint,
	type RotatedSecret,
	type WebhookDeliveryEntity,
	type WebhookEndpointEntity,
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

export interface CreateEndpointParams {
	name: string;
	targetUrl?: string | null;
	eventTypes?: string[];
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
	active?: boolean;
}

/**
 * Update an endpoint's configuration. Returns the endpoint in its post-edit
 * shape (never a secret). Only the fields present on `params` are sent, so an
 * omitted field is left as-is — and an empty `eventTypes` array is a real value
 * (subscribe to every relayable type), distinct from omitting it.
 */
export async function updateEndpoint(
	endpointId: string,
	params: UpdateEndpointParams,
): Promise<WebhookEndpointEntity> {
	const requestBody: {
		name?: string;
		target_url?: string | null;
		event_types?: string[];
		active?: boolean;
	} = {};
	if (params.name !== undefined) requestBody.name = params.name;
	if (params.targetUrl !== undefined) {
		requestBody.target_url = params.targetUrl?.trim() ? params.targetUrl.trim() : null;
	}
	if (params.eventTypes !== undefined) requestBody.event_types = params.eventTypes;
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
