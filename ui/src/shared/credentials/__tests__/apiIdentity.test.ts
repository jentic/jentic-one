import { describe, expect, it } from 'vitest';
import { apiRefKey, apiScopeCovers } from '@/shared/credentials/lib/apiIdentity';

describe('apiRefKey', () => {
	it('keys the raw and stored spellings of one API the same', () => {
		expect(apiRefKey({ vendor: 'httpbin.org', name: 'HTTPBin API' })).toBe(
			apiRefKey({ vendor: 'httpbin-org', name: 'httpbin-api' }),
		);
	});

	it('keeps distinct APIs distinct', () => {
		expect(apiRefKey({ vendor: 'slack.com', name: 'api' })).not.toBe(
			apiRefKey({ vendor: 'github.com', name: 'api' }),
		);
	});
});

describe('apiScopeCovers', () => {
	// The backend compares slugs (`credential_covers` slugifies both sides), so a
	// scope filed with a raw domain must still cover the stored row — answering
	// no here hides a credential the broker would have resolved.
	it('covers across the raw-vs-slug spelling of the same API', () => {
		expect(
			apiScopeCovers(
				{ vendor: 'httpbin.org', name: 'httpbin.org' },
				{ vendor: 'httpbin-org', name: 'httpbin-org', version: '1.0.0' },
			),
		).toBe(true);
	});

	it('treats an absent or empty axis as a wildcard', () => {
		const ref = { vendor: 'slack-com', name: 'api', version: '2.0.0' };
		expect(apiScopeCovers({ vendor: 'slack.com' }, ref)).toBe(true);
		expect(apiScopeCovers({ vendor: 'slack.com', name: '', version: '' }, ref)).toBe(true);
		expect(apiScopeCovers({ vendor: 'slack.com', name: null, version: null }, ref)).toBe(true);
	});

	it('does not cover a different vendor or name', () => {
		const ref = { vendor: 'slack-com', name: 'api', version: '2.0.0' };
		expect(apiScopeCovers({ vendor: 'github.com' }, ref)).toBe(false);
		expect(apiScopeCovers({ vendor: 'slack.com', name: 'admin' }, ref)).toBe(false);
	});

	it('holds a pinned version to one revision, never slugified', () => {
		const ref = { vendor: 'slack-com', name: 'api', version: '1.1.4' };
		expect(apiScopeCovers({ vendor: 'slack-com', version: '1.1.4' }, ref)).toBe(true);
		expect(apiScopeCovers({ vendor: 'slack-com', version: ' 1.1.4 ' }, ref)).toBe(true);
		expect(apiScopeCovers({ vendor: 'slack-com', version: '2.0.0' }, ref)).toBe(false);
		// Slugifying would have turned both into `1-1-4` and matched anything.
		expect(apiScopeCovers({ vendor: 'slack-com', version: '1-1-4' }, ref)).toBe(false);
	});

	it('does not let a pinned scope cover a version-less reference', () => {
		expect(
			apiScopeCovers(
				{ vendor: 'slack-com', version: '1.1.4' },
				{ vendor: 'slack-com', name: 'api' },
			),
		).toBe(false);
	});
});
