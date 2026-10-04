import { describe, it, expect } from 'vitest';
import {
	credentialsCoveringEntry,
	readyCredentialsFor,
	readyCredentialsForEntry,
	workspaceHrefFor,
} from '@/modules/discover/lib/catalogRelations';
import type { Credential } from '@/shared/credentials/api';

function cred(id: string, catalogApiId: string | null, active = true): Credential {
	return {
		credential_id: id,
		name: id,
		type: 'api_key',
		api: { vendor: 'nytimes.com', name: 'article_search', version: '1.0.0' },
		catalog_api_id: catalogApiId,
		provider: 'static',
		active,
		created_at: '2026-01-01T00:00:00Z',
	} as Credential;
}

describe('readyCredentialsFor', () => {
	const a = cred('cred_a', null);
	const b = cred('cred_b', 'nytimes.com/article_search');

	it('unions the covering credentials of every workspace API from the entry, once each', () => {
		expect(
			readyCredentialsFor([{ credentials: [a, b] }, { credentials: [b] }])?.map(
				(c) => c.credential_id,
			),
		).toEqual(['cred_a', 'cred_b']);
	});

	it('has nothing to match against without a workspace API for the entry', () => {
		expect(readyCredentialsFor(undefined)).toEqual([]);
		expect(readyCredentialsFor([])).toEqual([]);
	});

	it('returns null while any row is still waiting on the credential list', () => {
		expect(readyCredentialsFor([{ credentials: [a] }, { credentials: null }])).toBeNull();
	});
});

/** A credential scoped as the backend stores it (slugged vendor/name, `""` = wildcard). */
function scoped(
	id: string,
	api: { vendor: string; name?: string; version?: string },
	extra: Partial<Credential> = {},
): Credential {
	return {
		credential_id: id,
		name: id,
		type: 'api_key',
		api: { vendor: api.vendor, name: api.name ?? '', version: api.version ?? '' },
		catalog_api_id: null,
		provider: 'static',
		active: true,
		created_at: '2026-01-01T00:00:00Z',
		...extra,
	} as Credential;
}

const ARTICLE_SEARCH = { apiId: 'nytimes.com/article_search', catalogVendor: 'nytimes.com' };
const STRIPE = { apiId: 'stripe.com', catalogVendor: 'stripe.com' };

function ids(creds: Credential[] | null): string[] | null {
	return creds?.map((c) => c.credential_id) ?? null;
}

describe('credentialsCoveringEntry', () => {
	it('a vendor-wide credential (wildcard name/version) covers every API of that vendor', () => {
		const vendorWide = scoped('nyt_all', { vendor: 'nytimes-com' });
		expect(ids(credentialsCoveringEntry(ARTICLE_SEARCH, [vendorWide]))).toEqual(['nyt_all']);
		// Raw (unslugged) spelling of the same vendor compares equal, as on the backend.
		const raw = scoped('nyt_raw', { vendor: 'nytimes.com' });
		expect(ids(credentialsCoveringEntry(ARTICLE_SEARCH, [raw]))).toEqual(['nyt_raw']);
	});

	it('a credential created from the catalog pick (vendor + whole api_id as name) covers it', () => {
		const picked = scoped('nyt_pick', {
			vendor: 'nytimes-com',
			name: 'nytimes-com-article-search',
		});
		expect(ids(credentialsCoveringEntry(ARTICLE_SEARCH, [picked]))).toEqual(['nyt_pick']);
	});

	it('an exact catalog_api_id match counts even when the scope differs', () => {
		const exact = scoped(
			'nyt_exact',
			{ vendor: 'legacy-vendor', name: 'x', version: '9' },
			{ catalog_api_id: 'nytimes.com/article_search' },
		);
		expect(ids(credentialsCoveringEntry(ARTICLE_SEARCH, [exact]))).toEqual(['nyt_exact']);
	});

	it('never counts an inactive credential', () => {
		const off = scoped('off', { vendor: 'nytimes-com' }, { active: false });
		const offExact = scoped(
			'off_exact',
			{ vendor: 'x' },
			{ active: false, catalog_api_id: 'nytimes.com/article_search' },
		);
		expect(credentialsCoveringEntry(ARTICLE_SEARCH, [off, offExact])).toEqual([]);
	});

	it('does not match a different vendor', () => {
		expect(
			credentialsCoveringEntry(ARTICLE_SEARCH, [scoped('stripe', { vendor: 'stripe-com' })]),
		).toEqual([]);
		// Vendor comparison is exact on the slug — no suffix / prefix matching.
		expect(
			credentialsCoveringEntry(STRIPE, [scoped('api', { vendor: 'api-stripe-com' })]),
		).toEqual([]);
	});

	it('does not match a name-pinned credential for another API of the same vendor', () => {
		const books = scoped('nyt_books', { vendor: 'nytimes-com', name: 'nytimes-com-books' });
		const otherExact = scoped(
			'nyt_books_exact',
			{ vendor: 'nytimes-com', name: 'nytimes-com-books' },
			{ catalog_api_id: 'nytimes.com/books' },
		);
		expect(credentialsCoveringEntry(ARTICLE_SEARCH, [books, otherExact])).toEqual([]);
	});

	it('does not count a version-pinned scope (the version is unknown before import)', () => {
		const pinned = scoped('nyt_v1', { vendor: 'nytimes-com', version: '1.0.0' });
		expect(credentialsCoveringEntry(ARTICLE_SEARCH, [pinned])).toEqual([]);
	});

	it('is null (not "none") while the credential list is still loading', () => {
		expect(credentialsCoveringEntry(ARTICLE_SEARCH, null)).toBeNull();
		expect(credentialsCoveringEntry(ARTICLE_SEARCH, undefined)).toBeNull();
	});

	it('without a catalog vendor only the exact catalog id can match', () => {
		const entry = { apiId: 'example.org' };
		const vendorWide = scoped('ex', { vendor: 'example-org' });
		const exact = scoped('ex_exact', { vendor: 'y' }, { catalog_api_id: 'example.org' });
		expect(ids(credentialsCoveringEntry(entry, [vendorWide, exact]))).toEqual(['ex_exact']);
	});
});

describe('readyCredentialsForEntry', () => {
	const vendorWide = scoped('nyt_all', { vendor: 'nytimes-com' });
	const viaRow = cred('cred_row', null);

	it('unions the entry-level match with an older import’s credentials, once each', () => {
		expect(
			ids(
				readyCredentialsForEntry(
					ARTICLE_SEARCH,
					[{ credentials: [viaRow, vendorWide] }],
					[vendorWide],
				),
			),
		).toEqual(['nyt_all', 'cred_row']);
	});

	it('shows a genuinely un-imported entry (no workspace rows) as ready', () => {
		expect(ids(readyCredentialsForEntry(ARTICLE_SEARCH, undefined, [vendorWide]))).toEqual([
			'nyt_all',
		]);
	});

	it('is null until both the credential list and the rows have answered', () => {
		expect(readyCredentialsForEntry(ARTICLE_SEARCH, undefined, null)).toBeNull();
		expect(
			readyCredentialsForEntry(ARTICLE_SEARCH, [{ credentials: null }], [vendorWide]),
		).toBeNull();
	});
});

describe('workspaceHrefFor', () => {
	const hub = (v: string, catalogApiId: string | null = 'stripe.com') => ({
		href: `/library/workspace/stripe/stripe-api/${v}`,
		catalogApiId,
	});

	it('opens the hub when exactly one workspace API matches', () => {
		expect(workspaceHrefFor([hub('1')])).toBe('/library/workspace/stripe/stripe-api/1');
	});

	it('filters the workspace panel to the entry when several versions match', () => {
		expect(workspaceHrefFor([hub('1'), hub('2')])).toBe('/library?q=stripe.com');
	});

	it('falls back to the Library (never the retired workspace page) with no match', () => {
		expect(workspaceHrefFor(undefined)).toBe('/library');
		expect(workspaceHrefFor([])).toBe('/library');
		expect(workspaceHrefFor([hub('1', null), hub('2', null)])).toBe('/library');
	});
});
