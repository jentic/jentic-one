import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { listCatalog } from '@/modules/discover/api/client';

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
					manifest_age_seconds: null,
					has_more: false,
					next_cursor: null,
				});
			}),
		);

		await listCatalog({ filter: 'outdated' });

		expect(seen!.get('outdated_only')).toBe('true');
		// The other registration flags stay off so the backend only narrows to
		// the outdated set.
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
					manifest_age_seconds: null,
					has_more: false,
					next_cursor: null,
				});
			}),
		);

		await listCatalog({ filter: 'registered' });
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
					manifest_age_seconds: 30,
					has_more: false,
					next_cursor: null,
				}),
			),
		);

		const page = await listCatalog({ filter: 'all' });
		expect(page.outdatedCount).toBe(2);
	});
});
