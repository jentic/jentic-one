import { describe, expect, it } from 'vitest';
import * as api from '@/shared/api';

describe('the @/shared/api facade', () => {
	it('exports the configured client and its raw request for overlays', () => {
		expect(api.OpenAPI).toBeDefined();
		expect(typeof api.OpenAPI.BASE).toBe('string');
		expect(typeof api.apiRequest).toBe('function');
	});
});
