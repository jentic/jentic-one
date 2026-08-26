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
	allowed_cidrs: string[];
	active: boolean;
	created_at: string;
	previous_secret_expires_at: string | null;
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
	duration_ms: number | null;
	created_at: string;
}

interface AttemptRow {
	attempt_id: string;
	delivery_id: string;
	attempt_number: number;
	status_code: number | null;
	error: string | null;
	duration_ms: number | null;
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
			allowed_cidrs: [],
			active: true,
			created_at: now(-4320),
			previous_secret_expires_at: null,
		},
		{
			// A second, paused endpoint with no delivery history — exercises the
			// list's active/paused split in the overview strip and the "No
			// deliveries yet" (idle) health pill on a row.
			endpoint_id: 'whe_000000000000000000000002',
			name: 'pagerduty-escalations',
			target_url: 'https://events.example.com/integration/abc/enqueue',
			event_types: [],
			allowed_cidrs: [],
			active: false,
			created_at: now(-1440),
			previous_secret_expires_at: null,
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
			duration_ms: 142,
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
			// A real, categorised reason (the backend never stores free text here):
			// the status-carrying `http_error_<code>` category the UI renders as
			// "HTTP 500".
			last_error: 'http_error_500',
			duration_ms: 2310,
			created_at: now(-95),
		},
		{
			// A third row exercising the expressive-error rendering for the
			// motivating case — the endpoint's own IP allowlist rejecting the
			// resolved destination. Aged >24h so it does not shift the last-24h
			// KPIs (only the all-time total), and left `failed` (retrying) so it
			// is distinct from the dead-lettered row above.
			delivery_id: 'whd_000000000000000000000003',
			event_id: 'whv_000000000000000000000003',
			endpoint_id: 'whe_000000000000000000000001',
			status: 'failed',
			attempt_count: 2,
			next_attempt_at: now(5),
			last_attempt_at: now(-1560),
			last_status_code: null,
			last_error: 'blocked_by_allowlist',
			duration_ms: null,
			created_at: now(-1565),
		},
	];
}

function seedAttempts(): AttemptRow[] {
	return [
		{
			attempt_id: 'whda_00000000000000000000001',
			delivery_id: 'whd_000000000000000000000002',
			attempt_number: 1,
			status_code: 500,
			error: 'http_error_500',
			duration_ms: 2100,
			created_at: now(-94),
		},
		{
			attempt_id: 'whda_00000000000000000000002',
			delivery_id: 'whd_000000000000000000000002',
			attempt_number: 6,
			status_code: 500,
			error: 'http_error_500',
			duration_ms: 2310,
			created_at: now(-30),
		},
	];
}

let endpoints: EndpointRow[] = seedEndpoints();
let deliveries: DeliveryRow[] = seedDeliveries();
let attempts: AttemptRow[] = seedAttempts();
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
	attempts = seedAttempts();
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
			allowed_cidrs: body.allowed_cidrs ?? [],
			active: true,
			created_at: now(),
			previous_secret_expires_at: null,
		};
		endpoints = [row, ...endpoints];
		const secret = `whsec_${row.endpoint_id}_initial`; // pragma: allowlist secret
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
			allowed_cidrs: string[];
			active: boolean;
		}>;
		if ('target_url' in body) {
			const url = body.target_url;
			if (!url) return badRequest('A notification endpoint requires a target_url.');
			if (!url.startsWith('http://') && !url.startsWith('https://')) {
				return badRequest('target_url must be an http(s) URL.');
			}
			// Simulate the egress guard refusing a structurally-valid but
			// disallowed destination (e.g. a private/internal address). The
			// message is prefixed `target_url` so the UI pins it to the field.
			if (/blocked\.internal/i.test(url)) {
				return badRequest('target_url is not allowed: resolves to a private address.');
			}
			row.target_url = url;
		}
		if ('name' in body && body.name !== undefined) row.name = body.name;
		if ('event_types' in body && body.event_types !== undefined) {
			row.event_types = body.event_types;
		}
		if ('allowed_cidrs' in body && body.allowed_cidrs !== undefined) {
			row.allowed_cidrs = body.allowed_cidrs;
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

	http.get('/webhooks/endpoints/:id/stats', ({ params }) => {
		const rows = deliveries.filter((d) => d.endpoint_id === params.id);
		const countsByStatus: Record<string, number> = {};
		for (const r of rows) countsByStatus[r.status] = (countsByStatus[r.status] ?? 0) + 1;
		const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
		const recent = rows.filter((r) => Date.parse(r.created_at) >= dayAgo);
		const durations = rows.map((r) => r.duration_ms).filter((d): d is number => d != null);
		const withAttempt = rows
			.filter((r) => r.last_attempt_at)
			.sort((a, b) => Date.parse(b.last_attempt_at!) - Date.parse(a.last_attempt_at!));
		const last = withAttempt[0];
		const next = rows
			.filter((r) => r.next_attempt_at && (r.status === 'pending' || r.status === 'failed'))
			.sort((a, b) => Date.parse(a.next_attempt_at!) - Date.parse(b.next_attempt_at!))[0];
		return HttpResponse.json({
			total: rows.length,
			counts_by_status: countsByStatus,
			recent_total: recent.length,
			recent_failed: recent.filter((r) => r.status === 'dead' || r.status === 'failed')
				.length,
			last_status_code: last?.last_status_code ?? null,
			last_attempt_at: last?.last_attempt_at ?? null,
			last_duration_ms: last?.duration_ms ?? null,
			next_attempt_at: next?.next_attempt_at ?? null,
			avg_duration_ms:
				durations.length > 0
					? durations.reduce((a, b) => a + b, 0) / durations.length
					: null,
		});
	}),

	http.get('/webhooks/deliveries/:id/attempts', ({ params }) => {
		const rows = attempts
			.filter((a) => a.delivery_id === params.id)
			.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
		return HttpResponse.json({ data: rows });
	}),

	http.get('/webhooks/event-catalog', () =>
		HttpResponse.json({
			// A representative slice of the real catalog — enough for the picker's
			// drift check and grouping to exercise, mirroring the curated list.
			data: [
				'access_request.approved',
				'access_request.denied',
				'access_request.filed',
				'access_request.withdrawn',
				'agent.created',
				'agent.registration_approved',
				'agent.registration_denied',
				'agent.self_registered',
				'broker.pbac_denied',
				'broker.toolkit_binding_unserved',
				'catalog.update_available',
				'catalog.update_conflicts_overlay',
				'credential.bound_to_toolkit',
				'credential.connected',
				'credential.connection_failed',
				'credential.expired',
				'credential.expiring_soon',
				'credential.not_provisioned',
				'credential.refresh_failed',
				'credential.stored',
				'credential.unbound_from_toolkit',
				'credential.undecryptable',
				'execution.completed',
				'execution.failed',
				'execution.repeated_failure',
				'import.completed',
				'import.failed',
				'job.failed_permanently',
				'overlay.deprecated',
				'security.unauthorized_access_attempt',
				'toolkit.bound_to_agent',
				'toolkit.created',
				'toolkit.key_created',
				'toolkit.permission_rule_set',
				'toolkit.unbound_from_agent',
				'upstream.circuit_open',
			].map((event_type) => ({ event_type })),
		}),
	),

	// `:rotate-secret` and `:test` are colon-suffixed actions on the endpoint
	// path. MSW's matcher treats `:id` as a param up to the `/`, so the colon
	// action has to be part of the literal segment.
	http.post('/webhooks/endpoints/:id\\:rotate-secret', async ({ params, request }) => {
		const row = endpoints.find((e) => e.endpoint_id === params.id);
		if (!row) return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
		const body = (await request.json().catch(() => null)) as { grace_seconds?: number } | null;
		const graceSeconds = body?.grace_seconds ?? 86400;
		const secret = `whsec_${row.endpoint_id}_rotated_${idCounter}`; // pragma: allowlist secret
		secrets[row.endpoint_id] = secret;
		const previousExpiry =
			graceSeconds === 0 ? null : new Date(Date.now() + graceSeconds * 1000).toISOString();
		row.previous_secret_expires_at = previousExpiry;
		return HttpResponse.json({
			endpoint_id: row.endpoint_id,
			secret,
			// A zero grace period revokes at once, so there is no expiry to report.
			previous_secret_expires_at: previousExpiry,
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
			duration_ms: null,
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
