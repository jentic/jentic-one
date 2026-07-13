import { describe, it, expect } from 'vitest';
import { encodeApiId, formatApiKey } from '@/modules/workspace/api/apiId';

describe('apiId', () => {
	it('encodes a simple triple as slash-joined path segments', () => {
		const key = { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' };
		expect(encodeApiId(key)).toBe('stripe/stripe-api/2024-01-01');
	});

	it('percent-encodes each segment so an inner slash is not a separator', () => {
		const key = { vendor: 'acme/labs', name: 'my api', version: 'v1.0' };
		const id = encodeApiId(key);
		// The slash inside the vendor segment is encoded; the path still splits
		// into exactly three segments, each recoverable with decodeURIComponent
		// (mirroring how ApiDetailPage reads the route params back).
		const parts = id.split('/');
		expect(parts).toHaveLength(3);
		expect(parts.map(decodeURIComponent)).toEqual([key.vendor, key.name, key.version]);
	});

	it('formats a human label', () => {
		expect(formatApiKey({ vendor: 'stripe', name: 'stripe-api', version: '1' })).toBe(
			'stripe/stripe-api/1',
		);
	});
});
