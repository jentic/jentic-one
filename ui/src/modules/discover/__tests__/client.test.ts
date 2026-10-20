import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { catalogCursorAfter, listCatalog, refreshCatalog } from '@/modules/discover/api/client';

/**
 * Repository-tier coverage for the catalog list call. We can't `vi.spyOn` the
 * generated `CatalogService` in browser mode ("Cannot redefine property"), so
 * we assert the wire behaviour through MSW: what query params leave the client
 * and how the response's status fields are surfaced on the `CatalogPage`.
 */
describe('listCatalog', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	it("sends outdated_only=true when the filter is 'outdated'", async () => {
		let seen: URLSearchParams | null = null;
		worker.use(
			http.get('/catalog', ({ request }) => {
				seen = new URL(request.url).searchParams;
				return HttpResponse.json({
					data: [],
					catalog_total: 0,
					registered_count: 0,
					outdated_count: 0,
					oldest_source_sync_age_seconds: null,
					has_more: false,
					next_cursor: null,
				});
			}),
		);

		await listCatalog({ filter: 'outdated' });

		expect(seen!.get('outdated_only')).toBe('true');
		// The other registration flags stay off so the backend only narrows to
		// the outdated set (the UI never sets registered_only; the generated
		// client sends its `false` default).
		expect(seen!.get('registered_only')).toBe('false');
		expect(seen!.get('unregistered_only')).toBe('false');
	});

	it('leaves outdated_only off for the other filters', async () => {
		const captured: Record<string, string | null> = {};
		worker.use(
			http.get('/catalog', ({ request }) => {
				captured.outdatedOnly = new URL(request.url).searchParams.get('outdated_only');
				return HttpResponse.json({
					data: [],
					catalog_total: 0,
					registered_count: 0,
					outdated_count: 0,
					oldest_source_sync_age_seconds: null,
					has_more: false,
					next_cursor: null,
				});
			}),
		);

		await listCatalog({ filter: 'unregistered' });
		expect(captured.outdatedOnly).toBe('false');
	});

	it('surfaces outdated_count on the page (defaulting to 0 when absent)', async () => {
		worker.use(
			http.get('/catalog', () =>
				HttpResponse.json({
					data: [],
					catalog_total: 10,
					registered_count: 4,
					outdated_count: 2,
					oldest_source_sync_age_seconds: 30,
					has_more: false,
					next_cursor: null,
				}),
			),
		);

		const page = await listCatalog({ filter: 'all' });
		expect(page.outdatedCount).toBe(2);
	});
});

/**
 * Contract: the rail's jump cursor must match the backend's
 * `encode_catalog_cursor` (`src/jentic_one/shared/pagination.py`) byte for
 * byte — base64 of Python's `json.dumps({"id": entry_id})`. The expected tokens
 * below are that encoder's output; a format change on either side fails here.
 */
describe('catalogCursorAfter', () => {
	it.each([
		['y', 'eyJpZCI6ICJ5In0='],
		['{', 'eyJpZCI6ICJ7In0='],
		['googleapis.com/gmail', 'eyJpZCI6ICJnb29nbGVhcGlzLmNvbS9nbWFpbCJ9'],
		// json.dumps escapes non-ASCII (ensure_ascii) — so must we.
		['café.io/api', 'eyJpZCI6ICJjYWZcdTAwZTkuaW8vYXBpIn0='],
		['😀.dev', 'eyJpZCI6ICJcdWQ4M2RcdWRlMDAuZGV2In0='],
		['a"b\\c', 'eyJpZCI6ICJhXCJiXFxjIn0='],
	])('encodes %s like the backend', (apiId, token) => {
		expect(catalogCursorAfter(apiId)).toBe(token);
	});

	it('never throws on an id btoa cannot take raw (non-Latin-1)', () => {
		expect(() => catalogCursorAfter('日本.jp')).not.toThrow();
		expect(catalogCursorAfter('日本.jp')).not.toBeNull();
	});
});

describe('refreshCatalog', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	const source = (slug: string, enabled?: boolean) => ({
		slug,
		kind: 'http-index',
		visibility: 'public',
		status: 'ok',
		last_sync_at: null,
		live_entries: 0,
		consecutive_failures: 0,
		sync_pending: false,
		...(enabled === undefined ? {} : { enabled }),
	});

	function serve(failing: string[]): string[] {
		const synced: string[] = [];
		worker.use(
			http.get('/catalog/sources', () =>
				HttpResponse.json({
					data: [source('public', false), source('broken', true), source('corp')],
				}),
			),
			http.post('/catalog/sources/:action', ({ params }) => {
				const slug = String(params.action).replace(/:sync$/, '');
				synced.push(slug);
				if (failing.includes(slug)) {
					return HttpResponse.json({ detail: `${slug} down` }, { status: 500 });
				}
				return HttpResponse.json(
					{ slug, status: 'queued', forced: false, due_at: null },
					{ status: 202 },
				);
			}),
		);
		return synced;
	}

	it('skips disabled sources and reports a failure without aborting the rest', async () => {
		const synced = serve(['broken']);

		const result = await refreshCatalog();

		expect([...synced].sort()).toEqual(['broken', 'corp']);
		expect(result.skipped).toEqual(['public']);
		expect(result.requested.map((r) => r.slug)).toEqual(['corp']);
		expect(result.failed.map((f) => f.slug)).toEqual(['broken']);
		expect(result.failed[0].error.message).toBe('broken down');
	});

	it('throws when every sync request fails', async () => {
		serve(['broken', 'corp']);

		await expect(refreshCatalog()).rejects.toThrow(/down/);
	});
});
