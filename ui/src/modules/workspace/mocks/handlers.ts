/**
 * Workspace MSW handlers + fixtures.
 *
 * Mocks the registry surface the Workspace module consumes:
 *   GET  /apis                                   — list owned APIs (cursor page)
 *   GET  /apis/{v}/{n}/{ver}                      — single API
 *   GET  /apis/{v}/{n}/{ver}/operations           — current-revision operations
 *   GET  /apis/{v}/{n}/{ver}/revisions            — revision history
 *   POST /apis                                    — enqueue import (202 + job)
 *   POST /apis/{v}/{n}/{ver}/revisions/{id}:promote|:archive
 *   GET  /jobs/{id}                               — poll import job
 *
 * Shapes mirror the REAL wire payloads verified against the running backend on
 * :8000 (incl. the draft-only `no_current_revision` 404 and the async import →
 * job-poll flow), so the typed client + adapters deserialize them unchanged.
 *
 * Registered additively in src/mocks/handlers.ts (the sanctioned shared→module
 * bridge).
 */
import { http, HttpResponse } from 'msw';

function apiRef(vendor: string, name: string, version: string, host: string | null) {
	return { vendor, name, version, host };
}

/** A published API with a live revision + operations. */
const stripeApi = {
	api: apiRef('stripe', 'stripe-api', '2024-01-01', 'api.stripe.com'),
	display_name: 'Stripe',
	description: 'Payments, billing, and financial infrastructure APIs.',
	icon_url: null,
	current_revision_id: 'rev_stripe_live',
	revision_count: 2,
	operation_count: 3,
	security_schemes: ['bearer'],
	source: 'local',
	registered: true,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-02T00:00:00Z',
	_links: {
		self: `/apis/stripe/stripe-api/2024-01-01`,
		revisions: `/apis/stripe/stripe-api/2024-01-01/revisions`,
		current_revision: `/apis/stripe/stripe-api/2024-01-01/revisions/rev_stripe_live`,
		import: null,
	},
};

/** A draft-only API: no live revision → operations 404 with no_current_revision. */
const adyenApi = {
	api: apiRef('adyen', 'pos-terminal-management-api', '1', 'postfmapi-test.adyen.com'),
	display_name: null,
	description: null,
	icon_url: null,
	current_revision_id: null,
	revision_count: 1,
	operation_count: 5,
	security_schemes: [],
	source: 'local',
	registered: true,
	created_at: '2026-01-03T00:00:00Z',
	updated_at: '2026-01-03T00:00:00Z',
	_links: {
		self: `/apis/adyen/pos-terminal-management-api/1`,
		revisions: `/apis/adyen/pos-terminal-management-api/1/revisions`,
		current_revision: null,
		import: null,
	},
};

/**
 * A published API with MANY operations, so the cursor-paginated operations
 * endpoint returns multiple pages. Exercises the background "load every page"
 * walk, the page-of-25 paginator, and the "N of total" count messaging.
 */
const bigApi = {
	api: apiRef('bigco', 'big-api', '1', 'api.bigco.com'),
	display_name: 'BigCo',
	description: 'An API with lots of operations.',
	icon_url: null,
	current_revision_id: 'rev_big_live',
	revision_count: 1,
	operation_count: 60,
	security_schemes: [],
	source: 'local',
	registered: true,
	created_at: '2026-01-04T00:00:00Z',
	updated_at: '2026-01-04T00:00:00Z',
	_links: {
		self: `/apis/bigco/big-api/1`,
		revisions: `/apis/bigco/big-api/1/revisions`,
		current_revision: `/apis/bigco/big-api/1/revisions/rev_big_live`,
		import: null,
	},
};

const APIS = [stripeApi, adyenApi, bigApi];

const BIG_OPERATIONS = Array.from({ length: 60 }, (_, i) => ({
	operation_id: `Op${i}`,
	method: i % 2 === 0 ? 'get' : 'post',
	path: `/v1/resource/${i}`,
	name: `Operation ${i}`,
	description: null,
	tags: [],
	deprecated: false,
	revision_id: 'rev_big_live',
	_links: {},
}));

const STRIPE_OPERATIONS = [
	{
		operation_id: 'GetCharges',
		method: 'get',
		path: '/v1/charges',
		name: 'List charges',
		description: '<p>Returns a list of charges via <a href="/docs/charges">Charges</a>.</p>',
		tags: ['charges'],
		deprecated: false,
		revision_id: 'rev_stripe_live',
		_links: {},
	},
	{
		operation_id: 'CreateCharge',
		method: 'post',
		path: '/v1/charges',
		name: 'Create a charge',
		description: null,
		tags: ['charges'],
		deprecated: false,
		revision_id: 'rev_stripe_live',
		_links: {},
	},
	{
		operation_id: 'GetBalance',
		method: 'get',
		path: '/v1/balance',
		name: 'Retrieve balance',
		description: null,
		tags: ['balance'],
		deprecated: true,
		revision_id: 'rev_stripe_live',
		_links: {},
	},
];

function revision(
	apiKey: string,
	revisionId: string,
	state: string,
	isCurrent: boolean,
	opCount: number,
	origin: string | null = null,
) {
	const self = `/apis/${apiKey}/revisions/${revisionId}`;
	return {
		revision_id: revisionId,
		api:
			APIS.find((a) => `${a.api.vendor}/${a.api.name}/${a.api.version}` === apiKey)?.api ??
			null,
		source: {
			type: 'url',
			url: 'https://example.com/openapi.json',
			submitted_by: null,
		},
		spec_digest: `digest_${revisionId}`,
		operation_count: opCount,
		submitted_by: 'usr_admin_1',
		state,
		origin,
		is_current: isCurrent,
		promoted_at: isCurrent ? '2026-01-02T00:00:00Z' : null,
		archived_at: state === 'archived' ? '2026-01-02T00:00:00Z' : null,
		created_at: '2026-01-01T00:00:00Z',
		_links: {
			self,
			api: `/apis/${apiKey}`,
			promote: state === 'draft' ? `${self}:promote` : null,
			archive: state === 'draft' ? `${self}:archive` : null,
		},
	};
}

const REVISIONS: Record<string, ReturnType<typeof revision>[]> = {
	'stripe/stripe-api/2024-01-01': [
		revision('stripe/stripe-api/2024-01-01', 'rev_stripe_live', 'published', true, 3),
		revision('stripe/stripe-api/2024-01-01', 'rev_stripe_draft', 'draft', false, 4),
	],
	'adyen/pos-terminal-management-api/1': [
		revision('adyen/pos-terminal-management-api/1', 'rev_adyen_draft', 'draft', false, 5),
	],
	'bigco/big-api/1': [revision('bigco/big-api/1', 'rev_big_live', 'published', true, 60)],
};

/**
 * Overlays fixture (Stripe only): one confirmed-and-serving overlay, mirroring
 * the real wire shape incl. `created_by` (the authenticated principal),
 * `contributed_by` (free-text attribution), the raw `document`, and the
 * state-validity `_links`.
 */
const STRIPE_OVERLAYS = [
	{
		id: 'ovr_6a75aa8e6edd9723f71840e8',
		api_id: 'api_stripe',
		status: 'confirmed',
		document: {
			overlay: '1.0.0',
			info: { title: 'Overlay for Stripe', version: '1.0.0' },
			actions: [
				{
					description: 'Replace the US-only servers block with a regional template.',
					target: '$.servers',
					remove: true,
				},
			],
		},
		target_revision_id: null,
		confirmed_revision_id: 'rev_stripe_live',
		superseded_revision_id: 'rev_stripe_prev',
		contributed_by: 'contribute-spec-fix skill',
		created_by: 'usr_admin_1',
		confirmed_by_execution_id: null,
		created_at: '2026-01-05T00:00:00Z',
		updated_at: null,
		confirmed_at: '2026-01-05T01:00:00Z',
		deprecated_at: null,
		deprecated_reason: null,
		_links: {
			self: '/apis/stripe/stripe-api/2024-01-01/overlays/ovr_6a75aa8e6edd9723f71840e8',
			api: '/apis/stripe/stripe-api/2024-01-01',
			confirm: null,
			rollback:
				'/apis/stripe/stripe-api/2024-01-01/overlays/ovr_6a75aa8e6edd9723f71840e8:rollback',
			deprecate: '/apis/stripe/stripe-api/2024-01-01/overlays/ovr_6a75aa8e6edd9723f71840e8',
		},
	},
];

const OVERLAYS: Record<string, typeof STRIPE_OVERLAYS> = {
	'stripe/stripe-api/2024-01-01': STRIPE_OVERLAYS,
};

function cursorPage<T>(items: T[]) {
	return { data: items, has_more: false, next_cursor: null };
}

/**
 * Slice `items` into a cursor page. The cursor is the start offset encoded as a
 * string; `has_more`/`next_cursor` drive the client's background walk.
 */
function paginate<T>(items: T[], cursor: string | null, limit: number) {
	const start = cursor ? Number(cursor) : 0;
	const slice = items.slice(start, start + limit);
	const nextStart = start + slice.length;
	const hasMore = nextStart < items.length;
	return {
		data: slice,
		has_more: hasMore,
		next_cursor: hasMore ? String(nextStart) : null,
	};
}

function keyOf(params: Record<string, string | readonly string[] | undefined>): string {
	return `${params.vendor}/${params.name}/${params.version}`;
}

/** In-memory job table so a polled import transitions queued → succeeded. */
const jobs = new Map<string, { status: string; error: string | null; polls: number }>();

export const workspaceHandlers = [
	http.get(`/apis`, ({ request }) => {
		const url = new URL(request.url);
		const vendor = url.searchParams.get('vendor');
		let rows = APIS;
		if (vendor) rows = rows.filter((r) => r.api.vendor === vendor);
		return HttpResponse.json(cursorPage(rows));
	}),

	http.get(`/apis/:vendor/:name/:version`, ({ params }) => {
		const found = APIS.find(
			(a) => `${a.api.vendor}/${a.api.name}/${a.api.version}` === keyOf(params),
		);
		if (!found) {
			return HttpResponse.json(
				{ type: 'not_found', status: 404, detail: 'API not found' },
				{ status: 404 },
			);
		}
		return HttpResponse.json(found);
	}),

	http.delete(`/apis/:vendor/:name/:version`, ({ params }) => {
		const key = keyOf(params);
		const idx = APIS.findIndex((a) => `${a.api.vendor}/${a.api.name}/${a.api.version}` === key);
		if (idx < 0) {
			return HttpResponse.json(
				{ type: 'not_found', status: 404, detail: 'API not found' },
				{ status: 404 },
			);
		}
		APIS.splice(idx, 1);
		return new HttpResponse(null, { status: 204 });
	}),

	http.get(`/apis/:vendor/:name/:version/openapi`, ({ params }) => {
		const key = keyOf(params);
		const api = APIS.find((a) => `${a.api.vendor}/${a.api.name}/${a.api.version}` === key);
		if (!api || api.current_revision_id === null) {
			return HttpResponse.json(
				{
					type: 'no_current_revision',
					status: 404,
					detail: `API '${key}' has no current (live) revision`,
					instance: `/apis/${key}/openapi`,
				},
				{ status: 404 },
			);
		}
		return HttpResponse.json({
			openapi: '3.1.0',
			info: { title: api.display_name ?? key, version: String(params.version) },
			components: {
				securitySchemes: {
					bearerAuth: {
						type: 'http',
						scheme: 'bearer',
						description: 'Stripe secret API key sent as a Bearer token.',
					},
				},
			},
			security: [{ bearerAuth: [] }],
			paths: {
				'/v1/charges': {
					get: {
						operationId: 'GetCharges',
						summary: 'List charges',
						parameters: [
							{
								name: 'limit',
								in: 'query',
								required: false,
								description: 'A limit on the number of objects to return (1–100).',
							},
							{
								name: 'customer',
								in: 'query',
								required: false,
								description: 'Only return charges for this customer.',
							},
						],
					},
					post: {
						operationId: 'CreateCharge',
						summary: 'Create a charge',
						parameters: [
							{
								name: 'Idempotency-Key',
								in: 'header',
								required: true,
								description: 'Unique key to safely retry the request.',
							},
						],
					},
				},
			},
		});
	}),

	http.get(`/apis/:vendor/:name/:version/revisions/:revisionId/openapi`, ({ params }) => {
		const key = keyOf(params);
		const revisionId = String(params.revisionId);
		const rev = (REVISIONS[key] ?? []).find((r) => r.revision_id === revisionId);
		if (!rev) {
			return HttpResponse.json(
				{
					type: 'revision_not_found',
					status: 404,
					detail: `Revision '${revisionId}' not found for API '${key}'`,
					instance: `/apis/${key}/revisions/${revisionId}/openapi`,
				},
				{ status: 404 },
			);
		}
		return HttpResponse.json({
			openapi: '3.1.0',
			info: {
				title: `${key} (${rev.state})`,
				version: String(params.version),
				'x-revision-id': revisionId,
				'x-revision-state': rev.state,
			},
			paths: {
				'/v1/charges': {
					get: { operationId: 'GetCharges', summary: 'List charges' },
				},
			},
		});
	}),

	http.get(`/apis/:vendor/:name/:version/operations`, ({ params, request }) => {
		const key = keyOf(params);
		const api = APIS.find((a) => `${a.api.vendor}/${a.api.name}/${a.api.version}` === key);
		if (!api || api.current_revision_id === null) {
			return HttpResponse.json(
				{
					type: 'no_current_revision',
					status: 404,
					detail: `API '${key}' has no current (live) revision`,
					instance: `/apis/${key}/operations`,
				},
				{ status: 404 },
			);
		}
		const url = new URL(request.url);
		const cursor = url.searchParams.get('cursor');
		const limit = Number(url.searchParams.get('limit') ?? '25');
		// BigCo is the multi-page fixture; everything else fits a single page.
		const ops = key === 'bigco/big-api/1' ? BIG_OPERATIONS : STRIPE_OPERATIONS;
		return HttpResponse.json(paginate(ops, cursor, limit));
	}),

	http.get(`/apis/:vendor/:name/:version/revisions`, ({ params }) => {
		return HttpResponse.json(cursorPage(REVISIONS[keyOf(params)] ?? []));
	}),

	http.get(`/apis/:vendor/:name/:version/overlays`, ({ params }) => {
		return HttpResponse.json(cursorPage(OVERLAYS[keyOf(params)] ?? []));
	}),

	http.post(`/apis/:vendor/:name/:version/revisions/:revisionId`, ({ request }) => {
		// Matches the `:promote` / `:archive` action grammar appended to the id.
		const url = new URL(request.url);
		if (url.pathname.endsWith(':promote') || url.pathname.endsWith(':archive')) {
			return HttpResponse.json({ ok: true });
		}
		return new HttpResponse(null, { status: 404 });
	}),

	http.post(`/apis`, () => {
		const jobId = `job_${Math.random().toString(36).slice(2, 10)}`;
		jobs.set(jobId, { status: 'queued', error: null, polls: 0 });
		return HttpResponse.json(
			{ job_id: jobId, status: 'queued', _links: { self: `/jobs/${jobId}` } },
			{ status: 202 },
		);
	}),

	http.get(`/jobs/:jobId`, ({ params }) => {
		const jobId = String(params.jobId);
		const job = jobs.get(jobId) ?? { status: 'succeeded', error: null, polls: 99 };
		// Transition to succeeded after the first poll so the happy path resolves
		// quickly in dev/tests without hanging on a fake "queued" forever.
		job.polls += 1;
		if (job.polls >= 1 && job.status === 'queued') job.status = 'succeeded';
		jobs.set(jobId, job);
		return HttpResponse.json({
			job_id: jobId,
			kind: 'api_import',
			status: job.status,
			error: job.error,
			created_at: '2026-01-01T00:00:00Z',
			updated_at: '2026-01-01T00:00:01Z',
			_links: { self: `/jobs/${jobId}` },
		});
	}),
];
