import { describe, it, expect } from 'vitest';
import {
	toWorkspaceApi,
	toApiOperation,
	toApiRevision,
	toCursorPage,
} from '@/modules/workspace/api/adapters';

describe('workspace adapters', () => {
	it('maps an API row, tolerating missing catalog fields', () => {
		const api = toWorkspaceApi({
			api: { vendor: 'stripe', name: 'stripe-api', version: '1', host: 'api.stripe.com' },
			display_name: 'Stripe',
			description: null,
			icon_url: null,
			current_revision_id: 'rev_1',
			revision_count: 2,
			operation_count: 10,
			security_schemes: ['bearer'],
			created_at: '2026-01-01T00:00:00Z',
			updated_at: '2026-01-02T00:00:00Z',
		});
		expect(api.api.vendor).toBe('stripe');
		expect(api.displayName).toBe('Stripe');
		expect(api.currentRevisionId).toBe('rev_1');
		expect(api.securitySchemes).toEqual(['bearer']);
		// Catalog-era fields are absent on this branch's committed contract.
		expect(api.source).toBeUndefined();
		expect(api.registered).toBeUndefined();
	});

	it('reads catalog fields when the live backend includes them', () => {
		const api = toWorkspaceApi({
			api: { vendor: 'a', name: 'b', version: '1', host: null },
			display_name: null,
			description: null,
			icon_url: null,
			current_revision_id: null,
			revision_count: 0,
			operation_count: 0,
			security_schemes: [],
			source: 'local',
			registered: true,
			created_at: '',
			updated_at: '',
		});
		expect(api.source).toBe('local');
		expect(api.registered).toBe(true);
	});

	it('maps a cursor page', () => {
		const page = toCursorPage(
			{
				data: [{ operation_id: 'x', method: 'get', path: '/x' }],
				has_more: true,
				next_cursor: 'c1',
			},
			toApiOperation,
		);
		expect(page.items).toHaveLength(1);
		expect(page.hasMore).toBe(true);
		expect(page.nextCursor).toBe('c1');
	});

	it('lifts revision action links to the surface', () => {
		const rev = toApiRevision({
			revision_id: 'rev_draft',
			api: { vendor: 'a', name: 'b', version: '1', host: null },
			source: { type: 'url', url: 'https://x/openapi.json', submitted_by: null },
			spec_digest: 'abc',
			operation_count: 5,
			state: 'draft',
			is_current: false,
			promoted_at: null,
			archived_at: null,
			created_at: '2026-01-01T00:00:00Z',
			_links: {
				self: '/apis/a/b/1/revisions/rev_draft',
				promote: '/apis/a/b/1/revisions/rev_draft:promote',
				archive: '/apis/a/b/1/revisions/rev_draft:archive',
			},
		});
		expect(rev.state).toBe('draft');
		expect(rev.isCurrent).toBe(false);
		expect(rev.sourceUrl).toBe('https://x/openapi.json');
		expect(rev.promoteHref).toBe('/apis/a/b/1/revisions/rev_draft:promote');
		expect(rev.archiveHref).toBe('/apis/a/b/1/revisions/rev_draft:archive');
	});

	it('defaults gracefully on garbage input', () => {
		const api = toWorkspaceApi(null);
		expect(api.api.vendor).toBe('');
		expect(api.securitySchemes).toEqual([]);
		const page = toCursorPage(undefined, toApiOperation);
		expect(page.items).toEqual([]);
		expect(page.hasMore).toBe(false);
	});
});
