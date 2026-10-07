import { describe, it, expect } from 'vitest';
import { formatApiKey } from '@/modules/workspace/api/apiId';

// Hub URL encoding (an inner slash stays inside its segment) is covered by
// `ROUTE_PATHS.workspaceApiHub` in `src/__tests__/libraryRoutes.test.tsx`.
describe('apiId', () => {
	it('formats a human label', () => {
		expect(formatApiKey({ vendor: 'stripe', name: 'stripe-api', version: '1' })).toBe(
			'stripe/stripe-api/1',
		);
	});
});
