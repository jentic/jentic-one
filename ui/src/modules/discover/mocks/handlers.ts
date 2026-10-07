/**
 * Discover MSW handlers + fixtures.
 *
 * Mocks the catalog backend surface the Discover module consumes:
 *   GET  /catalog                       — keyset-paginated browse/search/filter
 *   GET  /catalog/{api_id}/operations   — operation preview
 *   POST /catalog:refresh               — force a manifest rebuild (ack)
 *   POST /catalog/{api_id}:import       — enqueue import (202)
 *
 * There is no blended `/apis` feed — Discover reads only the public catalog,
 * whose per-entry `registered` flag drives the "In your workspace" marker.
 *
 * Registered additively in src/mocks/handlers.ts (the sanctioned shared→module
 * bridge). Shapes mirror the generated response models so the typed client
 * deserializes them unchanged.
 */
import { http, HttpResponse } from 'msw';

interface CatalogFixture {
	api_id: string;
	vendor: string;
	registered: boolean;
	github: string | null;
	/**
	 * `{sub}/{version}` directories under `apis/openapi/{domain}/` — the real
	 * jentic-public-apis layout the backend's manifest builder emits (`sub` is
	 * the umbrella sub-API, or `main` when there is none).
	 */
	dirs: string;
}

const PUBLIC_APIS_RAW = 'https://raw.githubusercontent.com/jentic/jentic-public-apis/main';

function entry(f: CatalogFixture) {
	const domain = f.api_id.split('/')[0];
	const [sub] = f.dirs.split('/');
	return {
		api_id: f.api_id,
		vendor: f.vendor,
		path: sub === 'main' ? `apis/openapi/${domain}` : `apis/openapi/${domain}/${sub}`,
		spec_url: `${PUBLIC_APIS_RAW}/apis/openapi/${domain}/${f.dirs}/openapi.json`,
		registered: f.registered,
		update_available: false,
		_links: {
			self: `/catalog/${f.api_id}`,
			operations: `/catalog/${f.api_id}/operations`,
			import: `/catalog/${f.api_id}:import`,
			github: f.github,
		},
	};
}

const CATALOG_ENTRIES = [
	entry({
		api_id: 'stripe.com',
		dirs: 'main/2024-01-01',
		vendor: 'stripe',
		registered: true,
		github: 'https://github.com/jentic/catalog/blob/main/stripe.com.json',
	}),
	entry({
		api_id: 'github.com',
		dirs: 'main/1.1.4',
		vendor: 'github',
		registered: false,
		github: 'https://github.com/jentic/catalog/blob/main/github.com.json',
	}),
	entry({
		api_id: 'slack.com',
		dirs: 'main/1.7.0',
		vendor: 'slack',
		registered: false,
		github: null,
	}),
	// Umbrella vendor with multiple sub-APIs that share one vendor (`nytimes.com`).
	// These exercise the distinct-title fix: searching "nyt" must surface rows
	// tellable apart by title, not three identical "nytimes.com" rows.
	entry({
		api_id: 'nytimes.com/article_search',
		dirs: 'article_search/1.0.0',
		vendor: 'nytimes.com',
		registered: false,
		github: 'https://github.com/jentic/catalog/blob/main/nytimes.com/article_search.json',
	}),
	entry({
		api_id: 'nytimes.com/top_stories',
		dirs: 'top_stories/2.0.0',
		vendor: 'nytimes.com',
		registered: false,
		github: 'https://github.com/jentic/catalog/blob/main/nytimes.com/top_stories.json',
	}),
	entry({
		api_id: 'nytimes.com/books',
		dirs: 'books/3.0.0',
		vendor: 'nytimes.com',
		registered: false,
		github: 'https://github.com/jentic/catalog/blob/main/nytimes.com/books.json',
	}),
];

/**
 * Test/mock seam: flip catalog entries' real per-entry fields (`registered`,
 * `update_available`) so a scenario can line the catalog up with the seeded
 * registry — the backend derives both from the local registry.
 */
export function patchMockCatalogEntry(
	apiId: string,
	fields: { registered?: boolean; update_available?: boolean },
): void {
	const row = CATALOG_ENTRIES.find((e) => e.api_id === apiId);
	if (row) Object.assign(row, fields);
}

/** A lighter fixture for bulk catalog rows (dev scenarios). */
export interface MockCatalogRow {
	api_id: string;
	vendor: string;
	version: string;
	description?: string;
}

/** Scenario-added rows: their operation previews are generated (see below). */
const GENERATED = new Map<string, MockCatalogRow>();

/**
 * Dev/mock seam: append rows to the catalog (e.g. a big umbrella vendor with
 * many sub-APIs, vendors across the alphabet) so the ledger's grouping, A–Z
 * rail and paging can be reviewed. Same wire shape as the fixtures above.
 */
export function addMockCatalogEntries(rows: MockCatalogRow[]): void {
	for (const row of rows) {
		if (CATALOG_ENTRIES.some((e) => e.api_id === row.api_id)) continue;
		const sub = row.api_id.includes('/') ? row.api_id.split('/').slice(1).join('_') : 'main';
		CATALOG_ENTRIES.push(
			entry({
				api_id: row.api_id,
				dirs: `${sub}/${row.version}`,
				vendor: row.vendor,
				registered: false,
				github: `https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/${row.api_id}`,
			}),
		);
		GENERATED.set(row.api_id, row);
	}
}

/** A small believable operation list for a generated row (API-key auth in a header). */
function generatedOperations(row: MockCatalogRow) {
	const base = (row.api_id.split('/')[1] ?? row.api_id.split('.')[0]).replace(/[^a-z0-9]+/gi, '');
	const res = base.toLowerCase() || 'items';
	const ops = [
		['get', `/${res}`, `List ${res}`],
		['post', `/${res}`, `Create a ${res} item`],
		['get', `/${res}/{id}`, `Retrieve a ${res} item`],
		['patch', `/${res}/{id}`, `Update a ${res} item`],
		['delete', `/${res}/{id}`, `Delete a ${res} item`],
		['get', '/webhooks', 'List webhooks'],
		['post', '/webhooks', 'Subscribe to events'],
	].map(([method, path, summary], i) => ({
		method,
		path,
		summary,
		description: summary,
		operation_id: `${res}/${i}`,
		parameters: [],
		security: ['api_key'],
		tags: [path.startsWith('/webhooks') ? 'webhooks' : res],
	}));
	return {
		data: ops,
		total: ops.length,
		offset: 0,
		truncated: false,
		info: {
			title: row.api_id,
			version: row.version,
			description: row.description ?? null,
		},
		security_schemes: {
			api_key: { type: 'apiKey', in: 'header', name: 'X-API-Key', description: 'API key.' },
		},
	};
}

/** Test/mock seam: the vendor a catalog entry resolves to. */
export function mockCatalogVendor(apiId: string): string | null {
	return CATALOG_ENTRIES.find((e) => e.api_id === apiId)?.vendor ?? null;
}

const GITHUB_OPERATIONS = {
	data: [
		{
			method: 'get',
			path: '/repos/{owner}/{repo}',
			summary: 'Get a repository',
			description: 'Returns a single repository.',
			operation_id: 'repos/get',
			parameters: [
				{ name: 'owner', in: 'path', required: true, description: 'Account owner.' },
				{ name: 'repo', in: 'path', required: true, description: 'Repository name.' },
			],
			security: ['bearer'],
			tags: ['repos'],
		},
		{
			method: 'post',
			path: '/repos/{owner}/{repo}/issues',
			summary: 'Create an issue',
			description: 'Creates a new issue in a repository.',
			operation_id: 'issues/create',
			parameters: [
				{
					name: 'title',
					in: 'body',
					required: true,
					description: 'The title of the issue.',
				},
			],
			security: ['bearer'],
			tags: ['issues'],
		},
		{
			method: 'get',
			path: '/user',
			summary: 'Get the authenticated user',
			description: 'Returns the profile of the authenticated user.',
			operation_id: 'users/get-authenticated',
			parameters: [],
			security: ['bearer'],
			tags: ['users'],
		},
		{
			method: 'patch',
			path: '/user',
			summary: 'Update the authenticated user',
			description: 'Updates the authenticated user profile.',
			operation_id: 'users/update-authenticated',
			parameters: [],
			security: ['bearer'],
			tags: ['users'],
		},
		{
			method: 'get',
			path: '/repos/{owner}/{repo}/issues',
			summary: 'List repository issues',
			description: 'Lists issues in a repository.',
			operation_id: 'issues/list',
			parameters: [],
			security: ['bearer'],
			tags: ['issues'],
		},
		{
			method: 'delete',
			path: '/repos/{owner}/{repo}',
			summary: 'Delete a repository',
			description: 'Deletes a repository.',
			operation_id: 'repos/delete',
			parameters: [],
			security: ['bearer'],
			tags: ['repos'],
		},
	],
	total: 6,
	offset: 0,
	truncated: false,
	info: {
		title: 'GitHub API',
		version: '1.1.4',
		// Long, markdown-formatted description so the sheet exercises the
		// Markdown renderer + the 280-char "Show more / Show less" truncation.
		description:
			'The **GitHub REST API** lets you build integrations, retrieve data, and ' +
			'automate your workflows. It supports `repos`, `issues`, and `users` ' +
			'resources among many others. See the [developer docs](https://docs.github.com) ' +
			'for the full reference. This text is intentionally long so the detail sheet ' +
			'truncates it at a word boundary and offers a Show more toggle to expand the rest.',
	},
	security_schemes: {
		bearer: { type: 'http', scheme: 'bearer', description: 'HTTP Bearer token auth.' },
	},
};

/** The real service's browse cursor shape (`encode_catalog_cursor`). */
function encodeMockCursor(apiId: string): string {
	return btoa(JSON.stringify({ id: apiId }));
}

function decodeMockCursor(cursor: string): string {
	try {
		const id = (JSON.parse(atob(cursor)) as { id?: unknown }).id;
		return typeof id === 'string' ? id : '';
	} catch {
		return '';
	}
}

export const discoverHandlers = [
	http.get('/catalog', ({ request }) => {
		const url = new URL(request.url);
		const q = url.searchParams.get('q')?.toLowerCase() ?? '';
		const registeredOnly = url.searchParams.get('registered_only') === 'true';
		const unregisteredOnly = url.searchParams.get('unregistered_only') === 'true';

		// Browse is ordered by api_id (the real service's keyset order).
		let rows = q
			? CATALOG_ENTRIES
			: [...CATALOG_ENTRIES].sort((a, b) => (a.api_id < b.api_id ? -1 : 1));
		if (registeredOnly) rows = rows.filter((r) => r.registered);
		if (unregisteredOnly) rows = rows.filter((r) => !r.registered);
		if (q) rows = rows.filter((r) => r.api_id.toLowerCase().includes(q));
		const outdatedOnly = url.searchParams.get('outdated_only') === 'true';
		if (outdatedOnly) rows = rows.filter((r) => r.registered && r.update_available);

		// Keyset paging like the real service: the cursor is base64 of
		// `{"id": <last api_id>}` and the page starts strictly after it (so the
		// rail's jump cursors work here too). Search order isn't api_id order,
		// so it pages by position instead.
		const limit = Number(url.searchParams.get('limit') ?? 50);
		const cursor = url.searchParams.get('cursor');
		let offset = 0;
		if (cursor) {
			const after = decodeMockCursor(cursor);
			offset = q
				? rows.findIndex((r) => r.api_id === after) + 1
				: rows.findIndex((r) => r.api_id > after);
			if (offset < 0) offset = rows.length;
		}
		const page = rows.slice(offset, offset + limit);
		const hasMore = offset + limit < rows.length;
		return HttpResponse.json({
			data: page,
			catalog_total: CATALOG_ENTRIES.length,
			// Whole-manifest counts, recomputed per request (a scenario may flip
			// entries), like the real service's status fields.
			registered_count: CATALOG_ENTRIES.filter((e) => e.registered).length,
			outdated_count: CATALOG_ENTRIES.filter((e) => e.registered && e.update_available)
				.length,
			manifest_age_seconds: 120,
			has_more: hasMore,
			next_cursor: hasMore ? encodeMockCursor(page[page.length - 1].api_id) : null,
		});
	}),

	http.get('/catalog/:apiId/operations', ({ params, request }) => {
		const apiId = String(params.apiId);
		const generated = GENERATED.get(apiId);
		if (generated) return HttpResponse.json(generatedOperations(generated));
		if (apiId !== 'github.com') {
			return HttpResponse.json({
				data: [],
				total: 0,
				offset: 0,
				truncated: false,
				info: { title: apiId, version: null, description: null },
				security_schemes: {},
			});
		}

		// Mirror the backend's server-side filtering + offset/limit windowing so
		// the infinite query + server-side search/tag are exercised end to end.
		const url = new URL(request.url);
		const q = (url.searchParams.get('q') ?? '').trim().toLowerCase();
		const tag = url.searchParams.get('tag');
		const offset = Number(url.searchParams.get('offset') ?? 0);
		const limit = Number(url.searchParams.get('limit') ?? 200);

		let ops = GITHUB_OPERATIONS.data;
		if (tag)
			ops = ops.filter((op) => op.tags?.some((t) => t.toLowerCase() === tag.toLowerCase()));
		if (q) {
			ops = ops.filter((op) =>
				[op.method, op.path, op.summary, op.operation_id]
					.join(' ')
					.toLowerCase()
					.includes(q),
			);
		}

		const total = ops.length;
		const window = ops.slice(offset, offset + limit);
		return HttpResponse.json({
			...GITHUB_OPERATIONS,
			data: window,
			total,
			offset,
			truncated: offset + window.length < total,
		});
	}),

	http.post('/catalog:refresh', () => {
		// Force-rebuild ack. The real backend resets the manifest snapshot here;
		// the fixture just echoes a fresh count so the success toast renders.
		return HttpResponse.json({ count: CATALOG_ENTRIES.length, status: 'refreshed' });
	}),

	http.post('/catalog/*', ({ request }) => {
		// The import action is addressed as `/catalog/{api_id}:import` where
		// api_id may itself contain slashes (FastAPI `:path`). Match the whole
		// `/catalog/*` tail and strip the `:import` action suffix to recover the
		// api_id, rather than relying on segment params (which can't express the
		// colon-action grammar).
		const url = new URL(request.url);
		const tail = decodeURIComponent(url.pathname.replace(/^\/catalog\//, ''));
		if (!tail.endsWith(':import')) {
			return new HttpResponse(null, { status: 404 });
		}
		const apiId = tail.slice(0, -':import'.length);
		const jobId = `job_${apiId.replace(/\W/g, '_')}`;
		return HttpResponse.json(
			{ job_id: jobId, status: 'queued', _links: { self: `/jobs/${jobId}` } },
			{ status: 202 },
		);
	}),
];
