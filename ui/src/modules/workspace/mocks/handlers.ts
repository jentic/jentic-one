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

/**
 * KITCHEN-SINK SHOWCASE (manual self-checking): one API whose fixtures hit
 * every state the revisions & overlays UI can render — all seven overlay
 * lifecycles (pending / active / superseded / confirmed / rolled-back /
 * deprecated-still-serving / deprecated via manual + re-import + legacy null
 * reason), every revision origin (plain url import, catalog, upload/draft,
 * overlay materialization), the near-identical KSUID pair (short-id fix), the
 * unknown-author fallback, the capped ">3 actions" summary, every action
 * verb (description / replace-array / update-object / set-scalar / remove /
 * empty-update / unrecognized), and per-revision specs that actually differ
 * so the Diff view has real content. Browse it at
 * /app/workspace/apis/showcase/kitchen-sink/1.0.0 with VITE_ENABLE_MSW=1.
 */
const showcaseApi = {
	api: apiRef('showcase', 'kitchen-sink', '1.0.0', 'api.kitchen-sink.test'),
	display_name: 'Kitchen Sink (all states)',
	description:
		'MSW showcase — every overlay lifecycle, revision origin, and summary variant in one API.',
	icon_url: null,
	current_revision_id: 'rev_ks_live_overlay',
	revision_count: 5,
	operation_count: 21,
	security_schemes: ['bearer'],
	source: 'local',
	registered: true,
	created_at: '2026-08-01T09:00:00Z',
	updated_at: '2026-08-06T16:00:00Z',
	_links: {
		self: `/apis/showcase/kitchen-sink/1.0.0`,
		revisions: `/apis/showcase/kitchen-sink/1.0.0/revisions`,
		current_revision: `/apis/showcase/kitchen-sink/1.0.0/revisions/rev_ks_live_overlay`,
		import: null,
	},
};

const APIS = [stripeApi, adyenApi, bigApi, showcaseApi];

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
	overrides: Record<string, unknown> = {},
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
		...overrides,
	};
}

const KS_KEY = 'showcase/kitchen-sink/1.0.0';

/** Newest-first, like the wire. Every origin + state appears once. */
const KS_REVISIONS = [
	// A plain upload → draft serving nothing ("uploaded (draft)" in the header).
	revision(KS_KEY, 'rev_ks_draft_upload', 'draft', false, 22, 'uploaded', {
		created_at: '2026-08-06T16:00:00Z',
		source: { type: 'inline', filename: 'kitchen-sink-v2.yaml', submitted_by: 'usr_admin_1' },
	}),
	// The live revision, materialized from the ACTIVE overlay below.
	revision(KS_KEY, 'rev_ks_live_overlay', 'published', true, 21, 'overlay', {
		created_at: '2026-08-05T10:00:00Z',
		promoted_at: '2026-08-05T10:00:05Z',
	}),
	// Materialized from an overlay, then ROLLED BACK the same day (archived;
	// "same count" delta vs the catalog revision below).
	revision(KS_KEY, 'rev_ks_rolledback_overlay', 'archived', false, 20, 'overlay', {
		created_at: '2026-08-04T08:00:10Z',
		promoted_at: '2026-08-04T08:00:15Z',
		archived_at: '2026-08-04T18:00:00Z',
	}),
	// A catalog re-import that served for a while, then was superseded.
	revision(KS_KEY, 'rev_ks_catalog', 'archived', false, 20, 'catalog', {
		created_at: '2026-08-03T09:00:00Z',
		promoted_at: '2026-08-03T09:00:05Z',
		archived_at: '2026-08-05T10:00:05Z',
	}),
	// The original plain url import — first revision, so no diff base ("View spec").
	revision(KS_KEY, 'rev_ks_first_import', 'archived', false, 19, null, {
		created_at: '2026-08-01T09:00:00Z',
		archived_at: '2026-08-03T09:00:05Z',
		submitted_by: null, // pre-attribution row → author unknown
	}),
];

const REVISIONS: Record<string, ReturnType<typeof revision>[]> = {
	'stripe/stripe-api/2024-01-01': [
		revision('stripe/stripe-api/2024-01-01', 'rev_stripe_live', 'published', true, 3),
		revision('stripe/stripe-api/2024-01-01', 'rev_stripe_draft', 'draft', false, 4),
	],
	'adyen/pos-terminal-management-api/1': [
		revision('adyen/pos-terminal-management-api/1', 'rev_adyen_draft', 'draft', false, 5),
	],
	'bigco/big-api/1': [revision('bigco/big-api/1', 'rev_big_live', 'published', true, 60)],
	[KS_KEY]: KS_REVISIONS,
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

/**
 * Kitchen-sink overlays — one row per lifecycle the UI can badge. KSUID-style
 * ids share their LEADING characters on purpose (ids minted close together do
 * in reality): a naive 8-char truncation would render every row as
 * `ovr_9f31`, which is the id-collision bug the `ovr_…tail` short form fixes.
 *
 * NOTE: `active` and `deprecated · still serving` both require
 * `confirmed_revision_id === current`, which one real API can't exhibit at
 * once — tolerated here so a single page shows every badge. The active row
 * comes first so the revision list links the live revision to it.
 */
function ksOverlay(tail: string, overrides: Record<string, unknown>): Record<string, unknown> {
	const id = `ovr_9f31c07ad2b845e6${tail}`;
	const base = `/apis/${KS_KEY}/overlays/${id}`;
	return {
		id,
		api_id: 'api_kitchen_sink',
		status: 'confirmed',
		document: null,
		target_revision_id: null,
		confirmed_revision_id: null,
		superseded_revision_id: null,
		contributed_by: null,
		created_by: 'usr_admin_1',
		confirmed_by_execution_id: null,
		created_at: '2026-08-04T08:00:00Z',
		updated_at: null,
		confirmed_at: null,
		deprecated_at: null,
		deprecated_reason: null,
		_links: {
			self: base,
			api: `/apis/${KS_KEY}`,
			confirm: null,
			rollback: null,
			deprecate: base,
		},
		...overrides,
	};
}

const KS_OVERLAYS = [
	// ACTIVE — confirmed and its materialized revision is the one serving.
	// Author-written per-action descriptions are preferred by the summarizer.
	ksOverlay('a10001', {
		confirmed_revision_id: 'rev_ks_live_overlay',
		superseded_revision_id: 'rev_ks_catalog',
		contributed_by: 'contribute-spec-fix skill',
		created_at: '2026-08-05T09:55:00Z',
		confirmed_at: '2026-08-05T10:00:00Z',
		document: {
			overlay: '1.0.0',
			info: { title: 'Regional servers + delete op', version: '1.0.0' },
			actions: [
				{
					description:
						'Replace the US-only servers block with the EU+US regional template.',
					target: '$.servers',
					update: [{ url: 'https://eu.api.kitchen-sink.test' }],
				},
				{
					description: 'Document the missing DELETE /pets/{petId} operation.',
					target: "$.paths['/pets/{petId}']",
					update: { delete: {} },
				},
			],
		},
		_links: {
			self: `/apis/${KS_KEY}/overlays/ovr_9f31c07ad2b845e6a10001`,
			api: `/apis/${KS_KEY}`,
			confirm: null,
			rollback: `/apis/${KS_KEY}/overlays/ovr_9f31c07ad2b845e6a10001:rollback`,
			deprecate: `/apis/${KS_KEY}/overlays/ovr_9f31c07ad2b845e6a10001`,
		},
	}),
	// PENDING — awaiting confirm; author unknown (pre-fix row: no principal
	// captured, no free-text attribution). Derived array-update summary.
	ksOverlay('b20002', {
		status: 'pending',
		created_by: null,
		created_at: '2026-08-06T14:30:00Z',
		document: {
			overlay: '1.0.0',
			info: { title: 'Pending fix', version: '1.0.0' },
			actions: [
				{ target: '$.servers', update: [{ url: 'https://a' }, { url: 'https://b' }] },
			],
		},
		_links: {
			self: `/apis/${KS_KEY}/overlays/ovr_9f31c07ad2b845e6b20002`,
			api: `/apis/${KS_KEY}`,
			confirm: `/apis/${KS_KEY}/overlays/ovr_9f31c07ad2b845e6b20002:confirm`,
			rollback: null,
			deprecate: `/apis/${KS_KEY}/overlays/ovr_9f31c07ad2b845e6b20002`,
		},
	}),
	// SUPERSEDED — materialized, but a newer revision has since taken over.
	// >3 actions exercises the capped summary ("+2 more actions") and every
	// derived verb: update-object, set-scalar, empty-update, unrecognized.
	ksOverlay('c30003', {
		confirmed_revision_id: 'rev_ks_catalog',
		superseded_revision_id: 'rev_ks_first_import',
		created_by: 'sa_ci_bot',
		contributed_by: 'nightly spec-lint',
		created_at: '2026-08-03T08:50:00Z',
		confirmed_at: '2026-08-03T09:00:00Z',
		document: {
			overlay: '1.0.0',
			info: { title: 'Metadata cleanup', version: '1.0.0' },
			actions: [
				{ target: '$.info', update: { title: 'Kitchen Sink', contact: {} } },
				{ target: '$.info.version', update: '2.0.0' },
				{ description: 'Normalize tag casing across all operations.' },
				{ target: '$.paths', update: {} },
				{ target: '$.components' },
			],
		},
	}),
	// CONFIRMED — confirmed but not (yet) materialized into a revision.
	ksOverlay('d40004', {
		created_at: '2026-08-06T11:00:00Z',
		confirmed_at: '2026-08-06T11:05:00Z',
		document: {
			overlay: '1.0.0',
			info: { title: 'Version bump', version: '1.0.0' },
			actions: [{ target: '$.info.version', update: '2.1.0' }],
		},
	}),
	// ROLLED BACK — durable via deprecated_reason='rollback'; its superseded
	// revision was restored. Derived remove-summary.
	ksOverlay('e50005', {
		status: 'deprecated',
		confirmed_revision_id: 'rev_ks_rolledback_overlay',
		superseded_revision_id: 'rev_ks_catalog',
		created_at: '2026-08-04T07:55:00Z',
		confirmed_at: '2026-08-04T08:00:00Z',
		deprecated_at: '2026-08-04T18:00:00Z',
		deprecated_reason: 'rollback',
		document: {
			overlay: '1.0.0',
			info: { title: 'Drop legacy path', version: '1.0.0' },
			actions: [{ target: "$.paths['/legacy']", remove: true }],
		},
	}),
	// ROLLED BACK (legacy row) — deprecated before `deprecated_reason` existed
	// (reason null); recognized by its superseded revision being live again.
	ksOverlay('f60006', {
		status: 'deprecated',
		confirmed_revision_id: 'rev_ks_ghost',
		superseded_revision_id: 'rev_ks_live_overlay',
		contributed_by: 'legacy row — no reason persisted',
		created_at: '2026-08-02T10:00:00Z',
		confirmed_at: '2026-08-02T10:05:00Z',
		deprecated_at: '2026-08-02T12:00:00Z',
		document: {
			overlay: '1.0.0',
			info: { title: 'Old experiment', version: '1.0.0' },
			actions: [{ target: '$.info.description', update: 'Experimental description.' }],
		},
	}),
	// DEPRECATED · STILL SERVING — status flipped to deprecated but nothing
	// replaced its revision, so the patched spec is still what's served.
	ksOverlay('a70007', {
		status: 'deprecated',
		confirmed_revision_id: 'rev_ks_live_overlay',
		superseded_revision_id: 'rev_ks_catalog',
		created_at: '2026-08-05T11:00:00Z',
		confirmed_at: '2026-08-05T11:05:00Z',
		deprecated_at: '2026-08-06T09:00:00Z',
		deprecated_reason: 'manual',
		document: {
			overlay: '1.0.0',
			info: { title: 'Auth scheme description', version: '1.0.0' },
			actions: [
				{
					target: '$.components.securitySchemes.bearerAuth.description',
					update: 'Send the API key as a Bearer token.',
				},
			],
		},
	}),
	// DEPRECATED (manual retire) — never materialized, retired by an operator.
	ksOverlay('b80008', {
		status: 'deprecated',
		created_at: '2026-08-01T10:00:00Z',
		deprecated_at: '2026-08-01T15:00:00Z',
		deprecated_reason: 'manual',
		document: {
			overlay: '1.0.0',
			info: { title: 'Rejected proposal', version: '1.0.0' },
			actions: [{ target: '$.paths', remove: true }],
		},
	}),
	// DEPRECATED (superseded by re-import) — a fresh upstream spec replaced it.
	ksOverlay('c90009', {
		status: 'deprecated',
		confirmed_revision_id: 'rev_ks_ghost2',
		superseded_revision_id: 'rev_ks_first_import',
		created_at: '2026-08-02T09:00:00Z',
		confirmed_at: '2026-08-02T09:05:00Z',
		deprecated_at: '2026-08-03T09:00:00Z',
		deprecated_reason: 'superseded_by_reimport',
		document: {
			overlay: '1.0.0',
			info: { title: 'Interim server fix', version: '1.0.0' },
			actions: [{ target: '$.servers[0].url', update: 'https://interim.kitchen-sink.test' }],
		},
	}),
];

/**
 * Per-revision OpenAPI documents for the kitchen-sink API, built so ADJACENT
 * revisions genuinely differ (servers swap, paths added/removed, info edits)
 * — the Diff view then shows real added/removed/changed entries instead of
 * an empty "No differences".
 */
function ksSpec(overrides: {
	title?: string;
	version?: string;
	servers: { url: string }[];
	extraPaths?: Record<string, unknown>;
	description?: string;
}) {
	return {
		openapi: '3.1.0',
		info: {
			title: overrides.title ?? 'Kitchen Sink',
			version: overrides.version ?? '1.0.0',
			...(overrides.description ? { description: overrides.description } : {}),
		},
		servers: overrides.servers,
		paths: {
			'/pets': {
				get: { operationId: 'ListPets', summary: 'List pets' },
				post: { operationId: 'CreatePet', summary: 'Create a pet' },
			},
			'/pets/{petId}': {
				get: { operationId: 'GetPet', summary: 'Get a pet' },
			},
			...(overrides.extraPaths ?? {}),
		},
	};
}

const KS_SPECS: Record<string, unknown> = {
	rev_ks_first_import: ksSpec({
		servers: [{ url: 'https://us.api.kitchen-sink.test' }],
	}),
	rev_ks_catalog: ksSpec({
		servers: [{ url: 'https://us.api.kitchen-sink.test' }],
		description: 'Imported from the public catalog.',
		extraPaths: { '/legacy': { get: { operationId: 'Legacy', deprecated: true } } },
	}),
	rev_ks_rolledback_overlay: ksSpec({
		servers: [{ url: 'https://us.api.kitchen-sink.test' }],
		description: 'Imported from the public catalog.',
		extraPaths: { '/health': { get: { operationId: 'Health' } } },
	}),
	rev_ks_live_overlay: ksSpec({
		servers: [
			{ url: 'https://eu.api.kitchen-sink.test' },
			{ url: 'https://us.api.kitchen-sink.test' },
		],
		description: 'Imported from the public catalog.',
		extraPaths: {
			'/legacy': { get: { operationId: 'Legacy', deprecated: true } },
			'/pets/{petId}/photos': { post: { operationId: 'UploadPhoto' } },
		},
	}),
	rev_ks_draft_upload: ksSpec({
		title: 'Kitchen Sink (v2 draft)',
		version: '2.0.0-draft',
		servers: [{ url: 'https://api.kitchen-sink.test' }],
		extraPaths: {
			'/orders': {
				get: { operationId: 'ListOrders' },
				post: { operationId: 'CreateOrder' },
			},
		},
	}),
};

const KS_OPERATIONS = Array.from({ length: 21 }, (_, i) => ({
	operation_id: `KitchenOp${i}`,
	method: i % 3 === 0 ? 'get' : i % 3 === 1 ? 'post' : 'delete',
	path: `/pets/op-${i}`,
	name: `Kitchen op ${i}`,
	description: null,
	tags: ['pets'],
	deprecated: false,
	revision_id: 'rev_ks_live_overlay',
	_links: {},
}));

const OVERLAYS: Record<string, unknown[]> = {
	'stripe/stripe-api/2024-01-01': STRIPE_OVERLAYS,
	[KS_KEY]: KS_OVERLAYS,
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
		// The kitchen-sink live spec must equal its live REVISION's spec, so the
		// header "View spec" diff (live vs previous revision) lines up.
		if (key === KS_KEY) {
			return HttpResponse.json(KS_SPECS[api.current_revision_id] as Record<string, unknown>);
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
		const ksSpecDoc = KS_SPECS[revisionId];
		if (ksSpecDoc) {
			return HttpResponse.json(ksSpecDoc as Record<string, unknown>);
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
		// BigCo is the multi-page fixture; the kitchen-sink has its own 21 ops;
		// everything else fits a single page.
		const ops =
			key === 'bigco/big-api/1'
				? BIG_OPERATIONS
				: key === KS_KEY
					? KS_OPERATIONS
					: STRIPE_OPERATIONS;
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
