/**
 * Webhooks repository tier.
 *
 * PROTOTYPE: the platform has no webhooks backend surface yet, so there is no
 * generated OpenAPI service to call. This repository speaks plain `fetch`
 * against the paths the module's MSW handlers mock (`/webhooks…`), keeping the
 * same repository shape as other modules so swapping in the generated client
 * later only touches this file.
 */
import type {
	EndpointCreateInput,
	EndpointCreateResult,
	EndpointPatch,
	EventTypeDef,
	RotateSecretResult,
	WebhookDeliveryEntity,
	WebhookDeliveryRow,
	WebhookEndpointEntity,
	WebhookEndpointRow,
} from '@/modules/webhooks/api/types';
import { toDeliveryEntity, toEndpointEntity } from '@/modules/webhooks/api/types';

/** Module sentinel error — views catch this, never raw fetch failures. */
export class WebhooksApiError extends Error {
	readonly status: number | null;

	constructor(message: string, status: number | null = null) {
		super(message);
		this.name = 'WebhooksApiError';
		this.status = status;
	}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	let res: Response;
	try {
		res = await fetch(path, {
			headers: { 'Content-Type': 'application/json' },
			...init,
		});
	} catch {
		throw new WebhooksApiError('Network error while contacting the webhooks API.');
	}
	if (!res.ok) {
		let detail = `Request failed (${res.status}).`;
		try {
			const body = (await res.json()) as { detail?: string };
			if (body.detail) detail = body.detail;
		} catch {
			// non-JSON error body — keep the generic message
		}
		throw new WebhooksApiError(detail, res.status);
	}
	if (res.status === 204) return undefined as T;
	return (await res.json()) as T;
}

export async function listEndpoints(): Promise<WebhookEndpointEntity[]> {
	const body = await request<{ data: WebhookEndpointRow[] }>('/webhooks');
	return body.data.map(toEndpointEntity);
}

export async function getEndpoint(id: string): Promise<WebhookEndpointEntity> {
	const row = await request<WebhookEndpointRow>(`/webhooks/${encodeURIComponent(id)}`);
	return toEndpointEntity(row);
}

export async function listEventTypes(): Promise<EventTypeDef[]> {
	const body = await request<{ data: EventTypeDef[] }>('/webhooks/event-types');
	return body.data;
}

export async function createEndpoint(input: EndpointCreateInput): Promise<EndpointCreateResult> {
	const body = await request<{
		endpoint: WebhookEndpointRow;
		secret: string | null;
		ping: EndpointCreateResult['ping'];
	}>('/webhooks', { method: 'POST', body: JSON.stringify(input) });
	return { endpoint: toEndpointEntity(body.endpoint), secret: body.secret, ping: body.ping };
}

export async function updateEndpoint(
	id: string,
	patch: EndpointPatch,
): Promise<WebhookEndpointEntity> {
	const row = await request<WebhookEndpointRow>(`/webhooks/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch),
	});
	return toEndpointEntity(row);
}

export async function pauseEndpoint(id: string): Promise<WebhookEndpointEntity> {
	const row = await request<WebhookEndpointRow>(`/webhooks/${encodeURIComponent(id)}/pause`, {
		method: 'POST',
	});
	return toEndpointEntity(row);
}

export async function resumeEndpoint(id: string): Promise<WebhookEndpointEntity> {
	const row = await request<WebhookEndpointRow>(`/webhooks/${encodeURIComponent(id)}/resume`, {
		method: 'POST',
	});
	return toEndpointEntity(row);
}

export async function rotateEndpointSecret(id: string): Promise<RotateSecretResult> {
	const body = await request<{ secret: string; secret_preview: string }>(
		`/webhooks/${encodeURIComponent(id)}/rotate-secret`,
		{ method: 'POST' },
	);
	return { secret: body.secret, secretPreview: body.secret_preview };
}

export async function deleteEndpoint(id: string): Promise<void> {
	await request<void>(`/webhooks/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function sendTestEvent(id: string, eventType: string): Promise<WebhookDeliveryEntity> {
	const body = await request<{ delivery: WebhookDeliveryRow }>(
		`/webhooks/${encodeURIComponent(id)}/test`,
		{ method: 'POST', body: JSON.stringify({ event_type: eventType }) },
	);
	return toDeliveryEntity(body.delivery);
}

export async function listDeliveries(
	id: string,
	filters: { status?: string | null; eventType?: string | null } = {},
): Promise<WebhookDeliveryEntity[]> {
	const q = new URLSearchParams();
	if (filters.status) q.set('status', filters.status);
	if (filters.eventType) q.set('event_type', filters.eventType);
	const suffix = q.size > 0 ? `?${q.toString()}` : '';
	const body = await request<{ data: WebhookDeliveryRow[] }>(
		`/webhooks/${encodeURIComponent(id)}/deliveries${suffix}`,
	);
	return body.data.map(toDeliveryEntity);
}

export async function redeliverDelivery(
	endpointId: string,
	deliveryId: string,
): Promise<WebhookDeliveryEntity> {
	const body = await request<{ delivery: WebhookDeliveryRow }>(
		`/webhooks/${encodeURIComponent(endpointId)}/deliveries/${encodeURIComponent(deliveryId)}/redeliver`,
		{ method: 'POST' },
	);
	return toDeliveryEntity(body.delivery);
}
