/**
 * Webhooks MSW handlers + in-memory store (PROTOTYPE — no backend yet).
 *
 * Serves the `/webhooks…` surface the module repository consumes, with a
 * mutable store so lifecycle transitions (pause → resume, rotate, redeliver)
 * are observable across calls. Delivery simulation is deterministic: an
 * endpoint whose URL contains "fail" refuses deliveries; everything else
 * succeeds. Registered additively in src/mocks/handlers.ts.
 */
import { http, HttpResponse } from 'msw';

type WireStatus = 'active' | 'paused' | 'disabled';
type DeliveryStatus = 'delivered' | 'pending' | 'failed' | 'exhausted';
type DestinationType = 'https' | 'slack';

interface AttemptRow {
	attempted_at: string;
	ok: boolean;
	http_status: number | null;
	error: string | null;
	latency_ms: number;
}

interface EndpointRow {
	id: string;
	name: string;
	url: string;
	description: string | null;
	destination_type: DestinationType;
	status: WireStatus;
	event_types: string[];
	secret_preview: string | null;
	consecutive_failures: number;
	created_at: string;
	updated_at: string | null;
}

interface DeliveryRow {
	id: string;
	endpoint_id: string;
	event_id: string;
	event_type: string;
	status: DeliveryStatus;
	attempts: AttemptRow[];
	next_attempt_at: string | null;
	request_headers: Record<string, string>;
	payload: unknown;
	response_body: string | null;
	is_test: boolean;
	is_redelivery: boolean;
	created_at: string;
}

/** Subscribable event catalog — mirrors `EventType` (shared/models/events.py). */
const EVENT_CATALOG: ReadonlyArray<{ type: string; group: string; description: string }> = [
	{
		type: 'execution.completed',
		group: 'execution',
		description: 'A brokered API call finished successfully.',
	},
	{ type: 'execution.failed', group: 'execution', description: 'A brokered API call failed.' },
	{
		type: 'execution.repeated_failure',
		group: 'execution',
		description: 'The same execution failed repeatedly.',
	},
	{
		type: 'credential.expiring_soon',
		group: 'credential',
		description: 'A stored credential is close to its expiry.',
	},
	{
		type: 'credential.expired',
		group: 'credential',
		description: 'A stored credential has expired.',
	},
	{
		type: 'credential.connection_failed',
		group: 'credential',
		description: 'A credential connection attempt failed.',
	},
	{
		type: 'credential.refresh_failed',
		group: 'credential',
		description: 'An OAuth credential could not be refreshed.',
	},
	{
		type: 'agent.self_registered',
		group: 'agent',
		description: 'An agent registered itself and awaits approval.',
	},
	{
		type: 'agent.registration_approved',
		group: 'agent',
		description: 'An operator approved an agent registration.',
	},
	{
		type: 'agent.registration_denied',
		group: 'agent',
		description: 'An operator denied an agent registration.',
	},
	{
		type: 'access_request.filed',
		group: 'access_request',
		description: 'An agent filed a request for API access.',
	},
	{
		type: 'access_request.approved',
		group: 'access_request',
		description: 'An access request was approved.',
	},
	{
		type: 'access_request.denied',
		group: 'access_request',
		description: 'An access request was denied.',
	},
	{
		type: 'catalog.update_available',
		group: 'catalog',
		description: 'An imported API spec has an upstream update.',
	},
	{ type: 'import.completed', group: 'import', description: 'An API spec import finished.' },
	{ type: 'import.failed', group: 'import', description: 'An API spec import failed.' },
	{
		type: 'job.failed_permanently',
		group: 'job',
		description: 'A background job exhausted its retries.',
	},
	{
		type: 'security.unauthorized_access_attempt',
		group: 'security',
		description: 'A caller was rejected by authentication.',
	},
];

const now = (offsetMin = 0) => new Date(Date.now() + offsetMin * 60_000).toISOString();

let idCounter = 100;
const nextId = (prefix: string) => `${prefix}_${String(idCounter++).padStart(21, '0')}`;

function samplePayload(eventType: string, eventId: string, createdAt: string): unknown {
	return {
		id: eventId,
		type: eventType,
		created_at: createdAt,
		data: {
			summary: `Sample ${eventType} event`,
			actor_id: 'agnt_active_1',
			actor_type: 'agent',
			trace_id: 'trc_0000000000000000000042',
		},
	};
}

/** Human-readable Slack Block Kit message a `slack` destination receives. */
function slackPayload(eventType: string, eventId: string): unknown {
	const emoji = eventType.includes('failed') || eventType.startsWith('security.') ? '🔴' : '🟢';
	return {
		text: `${emoji} jentic-one — ${eventType}`,
		blocks: [
			{
				type: 'section',
				text: {
					type: 'mrkdwn',
					text: `${emoji} *${eventType}*\nSample ${eventType} event · actor \`agnt_active_1\``,
				},
			},
			{
				type: 'context',
				elements: [
					{
						type: 'mrkdwn',
						text: `<https://jentic.local/app/events|View in jentic-one> · \`${eventId}\``,
					},
				],
			},
		],
	};
}

function payloadFor(row: EndpointRow, eventType: string, eventId: string, createdAt: string) {
	return row.destination_type === 'slack'
		? slackPayload(eventType, eventId)
		: samplePayload(eventType, eventId, createdAt);
}

function standardHeaders(deliveryId: string, at: string): Record<string, string> {
	return {
		'content-type': 'application/json',
		'webhook-id': deliveryId,
		'webhook-timestamp': String(Math.floor(Date.parse(at) / 1000)),
		'webhook-signature': 'v1,K5oZfzN95Z9UVu1EsfQmfVNQhnkZ2pj9o9NDN/H/pI4=',
		'user-agent': 'jentic-one-webhooks/0.1',
	};
}

/** Slack incoming webhooks are unsigned — the URL itself is the credential. */
function slackHeaders(): Record<string, string> {
	return {
		'content-type': 'application/json',
		'user-agent': 'jentic-one-webhooks/0.1',
	};
}

function headersFor(row: EndpointRow, deliveryId: string, at: string): Record<string, string> {
	return row.destination_type === 'slack' ? slackHeaders() : standardHeaders(deliveryId, at);
}

/** One finished delivery, `minutesAgo` back, ok or failed. */
function seedDelivery(
	endpoint: EndpointRow,
	eventType: string,
	minutesAgo: number,
	ok: boolean,
	opts: { attempts?: number; pending?: boolean; test?: boolean } = {},
): DeliveryRow {
	const id = nextId('whd');
	const createdAt = now(-minutesAgo);
	const attemptCount = opts.attempts ?? 1;
	const attempts: AttemptRow[] = [];
	for (let i = 0; i < attemptCount; i++) {
		const isLast = i === attemptCount - 1;
		const attemptOk = isLast ? ok && !opts.pending : false;
		attempts.push({
			attempted_at: now(-minutesAgo + i * 5),
			ok: attemptOk,
			http_status: attemptOk ? 200 : 503,
			error: attemptOk ? null : 'Upstream answered 503 Service Unavailable',
			latency_ms: attemptOk ? 40 + ((i * 37) % 180) : 900 + ((i * 53) % 1200),
		});
	}
	const status: DeliveryStatus = opts.pending
		? 'pending'
		: ok
			? 'delivered'
			: attemptCount >= 5
				? 'exhausted'
				: 'failed';
	const okBody = endpoint.destination_type === 'slack' ? 'ok' : '{"received":true}';
	return {
		id,
		endpoint_id: endpoint.id,
		event_id: nextId('evt'),
		event_type: eventType,
		status,
		attempts,
		next_attempt_at: opts.pending ? now(30) : null,
		request_headers: headersFor(endpoint, id, createdAt),
		payload: payloadFor(endpoint, eventType, `evt_${id.slice(4)}`, createdAt),
		response_body: ok && !opts.pending ? okBody : 'upstream error: service unavailable',
		is_test: opts.test ?? false,
		is_redelivery: false,
		created_at: createdAt,
	};
}

/** Mutable per-session store. */
let endpoints: EndpointRow[] = [];
let deliveries: DeliveryRow[] = [];

function seedStore(): void {
	const slack: EndpointRow = {
		id: 'whe_000000000000000slack1',
		name: '#jentic-ops Slack channel',
		url: 'https://hooks.slack.com/services/T024BE7LD/B0G9QF2H3/XxYyZz0123456789abcdef',
		description: 'Notifies #jentic-ops about approvals and credential problems.',
		destination_type: 'slack',
		status: 'active',
		event_types: [
			'agent.self_registered',
			'access_request.filed',
			'credential.expiring_soon',
			'credential.expired',
		],
		secret_preview: null,
		consecutive_failures: 0,
		created_at: now(-60 * 24 * 12),
		updated_at: null,
	};
	const siem: EndpointRow = {
		id: 'whe_0000000000000000siem1',
		name: 'SIEM export',
		url: 'https://siem.acme-fail.example.com/ingest/jentic',
		description: 'Streams every platform event into the security lake.',
		destination_type: 'https',
		status: 'active',
		event_types: ['*'],
		secret_preview: 'whsec_9f8e7d6c…',
		consecutive_failures: 4,
		created_at: now(-60 * 24 * 30),
		updated_at: now(-60 * 24 * 2),
	};
	const staging: EndpointRow = {
		id: 'whe_00000000000000stagin1',
		name: 'Staging mirror',
		url: 'https://staging.acme.dev/hooks/jentic',
		description: null,
		destination_type: 'https',
		status: 'paused',
		event_types: ['execution.completed', 'execution.failed'],
		secret_preview: 'whsec_55aa66bb…',
		consecutive_failures: 0,
		created_at: now(-60 * 24 * 4),
		updated_at: null,
	};
	endpoints = [slack, siem, staging];
	deliveries = [
		seedDelivery(slack, 'agent.self_registered', 30, true),
		seedDelivery(slack, 'access_request.filed', 95, true),
		seedDelivery(slack, 'credential.expiring_soon', 60 * 5, true),
		seedDelivery(slack, 'access_request.filed', 60 * 9, true),
		seedDelivery(slack, 'agent.self_registered', 60 * 14, true),
		seedDelivery(slack, 'credential.expired', 60 * 30, false, { attempts: 2 }),
		seedDelivery(slack, 'access_request.filed', 60 * 44, true),
		seedDelivery(siem, 'execution.failed', 12, false, { attempts: 2, pending: true }),
		seedDelivery(siem, 'execution.completed', 45, false, { attempts: 3 }),
		seedDelivery(siem, 'security.unauthorized_access_attempt', 70, false, { attempts: 5 }),
		seedDelivery(siem, 'execution.completed', 60 * 3, false, { attempts: 4 }),
		seedDelivery(siem, 'toolkit.created', 60 * 26, true),
		seedDelivery(siem, 'execution.completed', 60 * 28, true),
		seedDelivery(siem, 'import.completed', 60 * 50, true),
		seedDelivery(staging, 'execution.completed', 60 * 24 * 2, true),
		seedDelivery(staging, 'execution.failed', 60 * 24 * 2 + 40, true),
	];
}

seedStore();

/** Reset hook for tests. */
export function resetWebhooksStore(): void {
	seedStore();
}

// ---------------------------------------------------------------------------
// Derived stats + simulation
// ---------------------------------------------------------------------------

const DAY_MS = 24 * 60 * 60 * 1000;

function withStats(row: EndpointRow) {
	const recent = deliveries.filter(
		(d) => d.endpoint_id === row.id && Date.now() - Date.parse(d.created_at) < DAY_MS,
	);
	const settled = recent.filter((d) => d.status !== 'pending');
	const okCount = settled.filter((d) => d.status === 'delivered').length;
	const all = deliveries
		.filter((d) => d.endpoint_id === row.id)
		.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
	const last = all[0] ?? null;
	return {
		...row,
		deliveries_24h: recent.length,
		success_rate_24h: settled.length > 0 ? okCount / settled.length : null,
		last_delivery_at: last?.created_at ?? null,
		last_delivery_ok: last ? last.status === 'delivered' : null,
	};
}

/** Deterministic delivery simulation: URLs containing "fail" refuse events. */
function endpointRefuses(row: EndpointRow): boolean {
	return row.url.includes('fail');
}

function attemptNow(ok: boolean): AttemptRow {
	return {
		attempted_at: now(),
		ok,
		http_status: ok ? 200 : 503,
		error: ok ? null : 'Upstream answered 503 Service Unavailable',
		latency_ms: ok
			? 40 + Math.floor(Math.random() * 160)
			: 900 + Math.floor(Math.random() * 900),
	};
}

function deliverNew(
	row: EndpointRow,
	eventType: string,
	opts: { test?: boolean; redelivery?: boolean } = {},
): DeliveryRow {
	const ok = !endpointRefuses(row);
	const id = nextId('whd');
	const createdAt = now();
	const okBody = row.destination_type === 'slack' ? 'ok' : '{"received":true}';
	const delivery: DeliveryRow = {
		id,
		endpoint_id: row.id,
		event_id: nextId('evt'),
		event_type: eventType,
		status: ok ? 'delivered' : 'failed',
		attempts: [attemptNow(ok)],
		next_attempt_at: ok ? null : now(1),
		request_headers: headersFor(row, id, createdAt),
		payload: payloadFor(row, eventType, `evt_${id.slice(4)}`, createdAt),
		response_body: ok ? okBody : 'upstream error: service unavailable',
		is_test: opts.test ?? false,
		is_redelivery: opts.redelivery ?? false,
		created_at: createdAt,
	};
	deliveries.unshift(delivery);
	row.consecutive_failures = ok ? 0 : row.consecutive_failures + 1;
	return delivery;
}

function notFound() {
	return HttpResponse.json({ detail: 'Webhook endpoint not found.' }, { status: 404 });
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

export const webhooksHandlers = [
	// Declared before `/webhooks/:id` so "event-types" is never read as an id.
	http.get('/webhooks/event-types', () => HttpResponse.json({ data: EVENT_CATALOG })),

	http.get('/webhooks', () => HttpResponse.json({ data: endpoints.map(withStats) })),

	http.post('/webhooks', async ({ request }) => {
		const body = (await request.json()) as {
			name?: string;
			url?: string;
			description?: string | null;
			destination_type?: DestinationType;
			event_types?: string[];
		};
		const destinationType: DestinationType = body.destination_type ?? 'https';
		if (!body.name?.trim() || !body.url?.trim()) {
			return HttpResponse.json({ detail: 'Name and URL are required.' }, { status: 422 });
		}
		if (!body.url.startsWith('https://')) {
			return HttpResponse.json({ detail: 'Endpoint URLs must use HTTPS.' }, { status: 422 });
		}
		if (destinationType === 'slack' && !body.url.startsWith('https://hooks.slack.com/')) {
			return HttpResponse.json(
				{ detail: 'Slack destinations need an incoming webhook URL (hooks.slack.com).' },
				{ status: 422 },
			);
		}
		if ((body.event_types ?? []).length === 0) {
			return HttpResponse.json(
				{ detail: 'Subscribe to at least one event type.' },
				{ status: 422 },
			);
		}
		const secretBody = Array.from({ length: 32 }, () =>
			'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.charAt(
				Math.floor(Math.random() * 62),
			),
		).join('');
		const row: EndpointRow = {
			id: nextId('whe'),
			name: body.name.trim(),
			url: body.url.trim(),
			description: body.description?.trim() || null,
			destination_type: destinationType,
			status: 'active',
			event_types: body.event_types ?? [],
			// Slack incoming webhooks are unsigned; only https endpoints get a secret.
			secret_preview: destinationType === 'slack' ? null : `whsec_${secretBody.slice(0, 8)}…`,
			consecutive_failures: 0,
			created_at: now(),
			updated_at: null,
		};
		endpoints.unshift(row);
		// GitHub-style ping on creation for instant reachability feedback.
		const ok = !endpointRefuses(row);
		const ping = {
			ok,
			http_status: ok ? 200 : 503,
			latency_ms: ok ? 40 + Math.floor(Math.random() * 160) : 1400,
			error: ok ? null : 'Upstream answered 503 Service Unavailable',
		};
		return HttpResponse.json(
			{
				endpoint: withStats(row),
				secret: destinationType === 'slack' ? null : `whsec_${secretBody}`,
				ping,
			},
			{ status: 201 },
		);
	}),

	http.get('/webhooks/:id', ({ params }) => {
		const row = endpoints.find((e) => e.id === params.id);
		return row ? HttpResponse.json(withStats(row)) : notFound();
	}),

	http.patch('/webhooks/:id', async ({ params, request }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		const patch = (await request.json()) as Partial<EndpointRow>;
		if (patch.url !== undefined && !String(patch.url).startsWith('https://')) {
			return HttpResponse.json({ detail: 'Endpoint URLs must use HTTPS.' }, { status: 422 });
		}
		if (
			patch.url !== undefined &&
			row.destination_type === 'slack' &&
			!String(patch.url).startsWith('https://hooks.slack.com/')
		) {
			return HttpResponse.json(
				{ detail: 'Slack destinations need an incoming webhook URL (hooks.slack.com).' },
				{ status: 422 },
			);
		}
		if (patch.name !== undefined) row.name = String(patch.name);
		if (patch.url !== undefined) row.url = String(patch.url);
		if (patch.description !== undefined) row.description = patch.description ?? null;
		if (patch.event_types !== undefined) row.event_types = patch.event_types;
		row.updated_at = now();
		return HttpResponse.json(withStats(row));
	}),

	http.post('/webhooks/:id/pause', ({ params }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		row.status = 'paused';
		row.updated_at = now();
		return HttpResponse.json(withStats(row));
	}),

	http.post('/webhooks/:id/resume', ({ params }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		row.status = 'active';
		row.consecutive_failures = 0;
		row.updated_at = now();
		return HttpResponse.json(withStats(row));
	}),

	http.post('/webhooks/:id/rotate-secret', ({ params }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		if (row.destination_type === 'slack') {
			return HttpResponse.json(
				{ detail: 'Slack destinations have no signing secret to rotate.' },
				{ status: 409 },
			);
		}
		const secretBody = Array.from({ length: 32 }, () =>
			'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.charAt(
				Math.floor(Math.random() * 62),
			),
		).join('');
		row.secret_preview = `whsec_${secretBody.slice(0, 8)}…`;
		row.updated_at = now();
		return HttpResponse.json({
			secret: `whsec_${secretBody}`,
			secret_preview: row.secret_preview,
		});
	}),

	http.delete('/webhooks/:id', ({ params }) => {
		const idx = endpoints.findIndex((e) => e.id === params.id);
		if (idx === -1) return notFound();
		endpoints.splice(idx, 1);
		deliveries = deliveries.filter((d) => d.endpoint_id !== params.id);
		return new HttpResponse(null, { status: 204 });
	}),

	http.post('/webhooks/:id/test', async ({ params, request }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		const body = (await request.json()) as { event_type?: string };
		const eventType = body.event_type ?? 'execution.completed';
		const delivery = deliverNew(row, eventType, { test: true });
		return HttpResponse.json({ delivery }, { status: 201 });
	}),

	http.get('/webhooks/:id/deliveries', ({ params, request }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		const url = new URL(request.url);
		const status = url.searchParams.get('status');
		const eventType = url.searchParams.get('event_type');
		const data = deliveries
			.filter((d) => d.endpoint_id === row.id)
			.filter((d) => (status ? d.status === status : true))
			.filter((d) => (eventType ? d.event_type === eventType : true))
			.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
		return HttpResponse.json({ data });
	}),

	http.post('/webhooks/:id/deliveries/:deliveryId/redeliver', ({ params }) => {
		const row = endpoints.find((e) => e.id === params.id);
		if (!row) return notFound();
		const original = deliveries.find(
			(d) => d.id === params.deliveryId && d.endpoint_id === row.id,
		);
		if (!original) {
			return HttpResponse.json({ detail: 'Delivery not found.' }, { status: 404 });
		}
		// Redelivery simulates the operator having fixed their receiver: it
		// succeeds even for otherwise-failing endpoints, and resets the counter.
		const id = nextId('whd');
		const createdAt = now();
		const delivery: DeliveryRow = {
			...original,
			id,
			status: 'delivered',
			attempts: [attemptNow(true)],
			next_attempt_at: null,
			request_headers: standardHeaders(id, createdAt),
			response_body: '{"received":true}',
			is_redelivery: true,
			created_at: createdAt,
		};
		deliveries.unshift(delivery);
		row.consecutive_failures = 0;
		return HttpResponse.json({ delivery }, { status: 201 });
	}),
];
