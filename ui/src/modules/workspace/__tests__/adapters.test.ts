import { describe, it, expect } from 'vitest';
import {
	toWorkspaceApi,
	toApiOperation,
	toApiRevision,
	toCursorPage,
	toOverlay,
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

	it('maps the Flow-3 catalog-linkage fields (origin/source_url/catalog_api_id/update_available)', () => {
		const api = toWorkspaceApi({
			api: { vendor: 'stripe.com', name: 'stripe-api', version: '1', host: null },
			display_name: null,
			description: null,
			icon_url: null,
			current_revision_id: 'rev_1',
			revision_count: 1,
			operation_count: 0,
			security_schemes: [],
			origin: 'catalog',
			source_url: 'https://example.com/openapi.json',
			catalog_api_id: 'nytimes.com/article_search',
			update_available: true,
			created_at: '',
			updated_at: '',
		});
		expect(api.origin).toBe('catalog');
		expect(api.sourceUrl).toBe('https://example.com/openapi.json');
		expect(api.catalogApiId).toBe('nytimes.com/article_search');
		expect(api.updateAvailable).toBe(true);
	});

	it('leaves Flow-3 fields undefined when the backend omits them', () => {
		const api = toWorkspaceApi({
			api: { vendor: 'a', name: 'b', version: '1', host: null },
			display_name: null,
			description: null,
			icon_url: null,
			current_revision_id: null,
			revision_count: 0,
			operation_count: 0,
			security_schemes: [],
			created_at: '',
			updated_at: '',
		});
		expect(api.origin).toBeUndefined();
		expect(api.sourceUrl).toBeUndefined();
		// catalog_api_id is #852's persisted column (always present, mapped via strOrNull),
		// so an omitted field yields null rather than undefined.
		expect(api.catalogApiId).toBeNull();
		expect(api.updateAvailable).toBeUndefined();
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

	it('maps revision origin + submitter (falling back to the source block)', () => {
		const rev = toApiRevision({
			revision_id: 'rev_ovl',
			api: { vendor: 'a', name: 'b', version: '1', host: null },
			source: { type: 'inline', submitted_by: 'usr_from_source' },
			spec_digest: 'abc',
			operation_count: 5,
			state: 'archived',
			origin: 'overlay',
			is_current: false,
			promoted_at: null,
			archived_at: '2026-01-02T00:00:00Z',
			created_at: '2026-01-01T00:00:00Z',
			_links: {},
		});
		expect(rev.origin).toBe('overlay');
		expect(rev.submittedBy).toBe('usr_from_source');

		const top = toApiRevision({
			revision_id: 'rev_x',
			source: { type: 'url', url: 'https://x', submitted_by: 'usr_nested' },
			submitted_by: 'usr_top',
			state: 'published',
			is_current: true,
			created_at: '',
			_links: {},
		});
		expect(top.submittedBy).toBe('usr_top');
		// Origin is null (not undefined) for a plain import.
		expect(top.origin).toBeNull();
	});

	it('maps an overlay row incl. author, attribution, document, and superseded revision', () => {
		const overlay = toOverlay({
			id: 'ovr_6a75aa8e6edd9723f71840e8',
			status: 'deprecated',
			document: { overlay: '1.0.0', actions: [{ target: '$.servers', remove: true }] },
			target_revision_id: null,
			confirmed_revision_id: 'rev_confirmed',
			superseded_revision_id: 'rev_superseded',
			contributed_by: 'contribute-spec-fix skill',
			created_by: 'usr_submitter',
			created_at: '2026-08-07T09:51:10Z',
			confirmed_at: '2026-08-07T09:52:00Z',
			deprecated_at: '2026-08-07T09:54:00Z',
			deprecated_reason: 'rollback',
			_links: { self: '/x', api: '/y', confirm: null, rollback: null, deprecate: null },
		});
		expect(overlay.id).toBe('ovr_6a75aa8e6edd9723f71840e8');
		expect(overlay.createdBy).toBe('usr_submitter');
		expect(overlay.contributedBy).toBe('contribute-spec-fix skill');
		expect(overlay.supersededRevisionId).toBe('rev_superseded');
		expect(overlay.deprecatedReason).toBe('rollback');
		expect(overlay.document).toMatchObject({ overlay: '1.0.0' });
		// Absent fields degrade to null, not undefined/crash.
		const bare = toOverlay({ id: 'ovr_x', status: 'pending', created_at: '' });
		expect(bare.createdBy).toBeNull();
		expect(bare.contributedBy).toBeNull();
		expect(bare.supersededRevisionId).toBeNull();
		expect(bare.deprecatedReason).toBeNull();
		expect(bare.document).toBeNull();
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
