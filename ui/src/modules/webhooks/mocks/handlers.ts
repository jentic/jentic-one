/**
 * Webhooks MSW handlers + in-memory store.
 *
 * Mirrors the management surface the module consumes, with a mutable store so
 * effects are observable across calls (queue a test event → a later delivery
 * GET shows the row; resend a dead row → it goes back to pending).
 *
 * Shapes and response codes mirror the real backend (verified on :8100):
 *
 *   POST   /webhooks/endpoints                 → 201 + endpoint + one-time secret
 *   GET    /webhooks/endpoints                 → 200, never any secret material
 *   PATCH  /webhooks/endpoints/{id}            → 200 + endpoint, never a secret
 *   DELETE /webhooks/endpoints/{id}            → 204 no body
 *   POST   .../{id}:rotate-secret              → 200 + a NEW secret
 *   POST   .../{id}:test                       → 202
 *   POST   /webhooks/deliveries/{id}:resend    → 202 no body
 *   missing target_url                           → 400
 *
 * This build ships **outbound notifications only**. Reads never return a secret:
 * the store keeps secrets in a side map that no GET response can reach, which is
 * what makes the "secret is never exposed on read" test meaningful rather than
 * tautological.
 *
 * Registered additively in src/mocks/handlers.ts.
 */
import { http, HttpResponse } from 'msw';

interface EndpointRow {
	endpoint_id: string;
	name: string;
	target_url: string | null;
	event_types: string[];
	active: boolean;
	created_at: string;
}

interface DeliveryRow {
	delivery_id: string;
	event_id: string;
	endpoint_id: string;
	status: 'pending' | 'succeeded' | 'failed' | 'dead';
	attempt_count: number;
	next_attempt_at: string | null;
	last_attempt_at: string | null;
	last_status_code: number | null;
	last_error: string | null;
	created_at: string;
}

const now = (offsetMin = 0) => new Date(Date.now() + offsetMin * 60_000).toISOString();

function seedEndpoints(): EndpointRow[] {
	return [
		{
			endpoint_id: 'whe_000000000000000000000001',
			name: 'slack-ops-alerts',
			target_url: 'https://hooks.example.com/services/T000/B000/XXXX',
			event_types: ['credential.expired', 'execution.failed'],
			active: true,
			created_at: now(-4320),
		},
	];
}

function seedDeliveries(): DeliveryRow[] {
	return [
		{
			delivery_id: 'whd_000000000000000000000001',
			event_id: 'whv_000000000000000000000001',
			endpoint_id: 'whe_000000000000000000000001',
			status: 'succeeded',
			attempt_count: 1,
			next_attempt_at: null,
			last_attempt_at: now(-120),
			last_status_code: 200,
			last_error: null,
			created_at: now(-121),
		},
		{
			delivery_id: 'whd_000000000000000000000002',
			event_id: 'whv_000000000000000000000002',
			endpoint_id: 'whe_000000000000000000000001',
			status: 'dead',
			attempt_count: 6,
			next_attempt_at: null,
			last_attempt_at: now(-30),
			last_status_code: 500,
			last_error: 'HTTP 500 from receiver',
			created_at: now(-95),
		},
	];
}

let endpoints: EndpointRow[] = seedEndpoints();
let deliveries: DeliveryRow[] = seedDeliveries();
/**
 * Secrets live here, NOT on the endpoint rows, so it is structurally impossible
 * for a GET handler to leak one — the same property the real backend has by
 * storing only an encrypted copy.
 */
let secrets: Record<string, string> = {};
let idCounter = 100;

export function resetWebhooksStore(): void {
	endpoints = seedEndpoints();
	deliveries = seedDeliveries();
	secrets = {};
	idCounter = 100;
}

/** The endpoints currently in the store (for assertions in tests). */
export function webhooksStoreEndpoints(): EndpointRow[] {
	return endpoints;
}

function nextId(prefix: string): string {
	idCounter += 1;
	return `${prefix}_${String(idCounter).padStart(24, '0')}`;
}

function badRequest(detail: string) {
	return HttpResponse.json({ detail }, { status: 400 });
}

export const webhooksHandlers = [
	http.get('/webhooks/endpoints', () => HttpResponse.json({ data: endpoints })),

	http.post('/webhooks/endpoints', async ({ request }) => {
		const body = (await request.json()) as Partial<EndpointRow>;
		if (!body.target_url) {
			return badRequest('A notification endpoint requires a target_url.');
		}

		const row: EndpointRow = {
			endpoint_id: nextId('whe'),
			name: body.name ?? 'unnamed',
			target_url: body.target_url ?? null,
			event_types: body.event_types ?? [],
			active: true,
			created_at: now(),
		};
		endpoints = [row, ...endpoints];
		const secret = `whsec_${row.endpoint_id}_initial`;
		secrets[row.endpoint_id] = secret;
		return HttpResponse.json({ endpoint: row, secret }, { status: 201 });
	}),

	http.get('/webhooks/endpoints/:id', ({ params }) => {
		const row = endpoints.find((e) => e.endpoint_id === params.id);
		if (!row) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		return HttpResponse.json(row);
	}),

	// PATCH is a partial update: only the fields present in the body are applied,
	// so an omitted field is left as-is. Never touches secrets and never returns
	// one — editing config is orthogonal to signing authority (that is rotate).
	http.patch('/webhooks/endpoints/:id', async ({ params, request }) => {
		const row = endpoints.find((e) => e.endpoint_id === params.id);
		if (!row) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		const body = (await request.json()) as Partial<{
			name: string;
			target_url: string | null;
			event_types: string[];
			active: boolean;
		}>;
		if ('target_url' in body) {
			const url = body.target_url;
			if (!url) return badRequest('A notification endpoint requires a target_url.');
			if (!url.startsWith('http://') && !url.startsWith('https://')) {
				return badRequest('target_url must be an http(s) URL.');
			}
			row.target_url = url;
		}
		if ('name' in body && body.name !== undefined) row.name = body.name;
		if ('event_types' in body && body.event_types !== undefined) {
			row.event_types = body.event_types;
		}
		if ('active' in body && body.active !== undefined) row.active = body.active;
		return HttpResponse.json(row);
	}),

	http.delete('/webhooks/endpoints/:id', ({ params }) => {
		const exists = endpoints.some((e) => e.endpoint_id === params.id);
		if (!exists) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		endpoints = endpoints.filter((e) => e.endpoint_id !== params.id);
		// Deleting an endpoint takes its delivery history with it.
		deliveries = deliveries.filter((d) => d.endpoint_id !== params.id);
		delete secrets[params.id as string];
		return new HttpResponse(null, { status: 204 });
	}),

	http.get('/webhooks/endpoints/:id/deliveries', ({ params }) => {
		const rows = deliveries.filter((d) => d.endpoint_id === params.id);
		return HttpResponse.json({ data: rows });
	}),

	// `:rotate-secret` and `:test` are colon-suffixed actions on the endpoint
	// path. MSW's matcher treats `:id` as a param up to the `/`, so the colon
	// action has to be part of the literal segment.
	http.post('/webhooks/endpoints/:id\\:rotate-secret', async ({ params, request }) => {
		const row = endpoints.find((e) => e.endpoint_id === params.id);
		if (!row) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		const body = (await request.json().catch(() => null)) as { grace_seconds?: number } | null;
		const graceSeconds = body?.grace_seconds ?? 86400;
		const secret = `whsec_${row.endpoint_id}_rotated_${idCounter}`;
		secrets[row.endpoint_id] = secret;
		return HttpResponse.json({
			endpoint_id: row.endpoint_id,
			secret,
			// A zero grace period revokes at once, so there is no expiry to report.
			previous_secret_expires_at:
				graceSeconds === 0
					? null
					: new Date(Date.now() + graceSeconds * 1000).toISOString(),
		});
	}),

	http.post('/webhooks/endpoints/:id\\:test', ({ params }) => {
		const row = endpoints.find((e) => e.endpoint_id === params.id);
		if (!row) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		const delivery: DeliveryRow = {
			delivery_id: nextId('whd'),
			event_id: nextId('whv'),
			endpoint_id: row.endpoint_id,
			status: 'pending',
			attempt_count: 0,
			next_attempt_at: now(),
			last_attempt_at: null,
			last_status_code: null,
			last_error: null,
			created_at: now(),
		};
		deliveries = [delivery, ...deliveries];
		return HttpResponse.json(
			{ delivery_id: delivery.delivery_id, event_type: 'webhook.test' },
			{ status: 202 },
		);
	}),

	http.post('/webhooks/deliveries/:id\\:resend', ({ params }) => {
		const row = deliveries.find((d) => d.delivery_id === params.id);
		if (!row) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		// Requeue: a dead-lettered row becomes eligible again, which is the point.
		row.status = 'pending';
		row.next_attempt_at = now();
		return new HttpResponse(null, { status: 202 });
	}),
];
