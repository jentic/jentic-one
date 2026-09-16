import { describe, it, expect } from 'vitest';
import { formatOperation } from '@/modules/monitor/lib/format';

describe('formatOperation', () => {
	it('renders method + path template when both are present', () => {
		expect(
			formatOperation({
				operation_id: 'op_abc123',
				operation_name: '/repos/{owner}/{repo}',
				operation_method: 'GET',
			}),
		).toBe('GET /repos/{owner}/{repo}');
	});

	it('renders the path template alone when the method is missing', () => {
		expect(
			formatOperation({
				operation_id: 'op_abc123',
				operation_name: '/v1/charges',
				operation_method: null,
			}),
		).toBe('/v1/charges');
	});

	it('falls back to the opaque operation_id on legacy rows', () => {
		expect(
			formatOperation({
				operation_id: 'op_abc123',
				operation_name: null,
				operation_method: null,
			}),
		).toBe('op_abc123');
	});

	it('returns null when the row has no operation identity at all', () => {
		expect(formatOperation({})).toBeNull();
	});
});
