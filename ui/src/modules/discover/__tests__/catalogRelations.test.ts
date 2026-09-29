import { describe, it, expect } from 'vitest';
import { readyCredentialsFor } from '@/modules/discover/lib/catalogRelations';
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
