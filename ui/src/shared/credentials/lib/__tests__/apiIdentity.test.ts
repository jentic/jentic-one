import { describe, it, expect } from 'vitest';
import { apiRefKey, pickKey, sharedApiLabels } from '@/shared/credentials/lib/apiIdentity';

describe('pickKey', () => {
	it('keys a workspace API by its registered vendor/name', () => {
		expect(pickKey({ source: 'local', vendor: 'github-com', name: 'api-github-com' })).toBe(
			'github-com/api-github-com',
		);
		expect(pickKey({ vendor: 'stripe.com', name: 'main' })).toBe(
			apiRefKey({ vendor: 'stripe.com', name: 'main' }),
		);
	});

	it('keys a catalog entry by the identity its import registers, so it matches the workspace API', () => {
		const entry = {
			source: 'catalog' as const,
			vendor: 'github.com',
			name: 'github.com/api.github.com',
			apiId: 'github.com/api.github.com',
		};
		expect(pickKey(entry)).toBe('github-com/api-github-com');
		// A bare-domain entry seeds its name from the whole id.
		expect(
			pickKey({
				source: 'catalog',
				vendor: 'github.com',
				name: 'api.github.com',
				apiId: 'api.github.com',
			}),
		).toBe('github-com/api-github-com');
		// A different sub-segment is a different API.
		expect(
			pickKey({
				source: 'catalog',
				vendor: 'github.com',
				name: 'github.com/ghec',
				apiId: 'github.com/ghec',
			}),
		).toBe('github-com/ghec');
	});

	it('falls back to the plain vendor/name key without a catalog id', () => {
		expect(pickKey({ source: 'catalog', vendor: 'acme.io', name: 'main' })).toBe(
			'acme-io/main',
		);
	});
});

describe('sharedApiLabels', () => {
	it('names only the labels more than one API carries, case and spacing aside', () => {
		const shared = sharedApiLabels([
			{ label: 'Orders Api' },
			{ label: 'orders api ' },
			{ label: 'Stripe' },
		]);
		expect([...shared]).toEqual(['orders api']);
	});
});
