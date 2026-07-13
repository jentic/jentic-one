import { describe, it, expect } from 'vitest';
import {
	deriveSuccessRate,
	isSuccessfulExecution,
	approxCountFromPage,
	formatApproxCount,
} from '@/modules/dashboard/api/types';
import type { ExecutionResponse } from '@/shared/api';

function exec(partial: Partial<ExecutionResponse>): ExecutionResponse {
	return {
		_links: { self: '/executions/x' },
		created_at: '2026-01-01T00:00:00Z',
		execution_id: 'x',
		started_at: '2026-01-01T00:00:00Z',
		status: 'completed',
		toolkit_id: 'tk',
		trace_id: 't',
		...partial,
	} as ExecutionResponse;
}

describe('dashboard derivations', () => {
	describe('isSuccessfulExecution', () => {
		it('treats 2xx http_status as success', () => {
			expect(isSuccessfulExecution(exec({ http_status: 200 }))).toBe(true);
			expect(isSuccessfulExecution(exec({ http_status: 204 }))).toBe(true);
		});

		it('treats 4xx/5xx http_status as failure regardless of status string', () => {
			expect(isSuccessfulExecution(exec({ http_status: 500, status: 'completed' }))).toBe(
				false,
			);
			expect(isSuccessfulExecution(exec({ http_status: 404 }))).toBe(false);
		});

		it('falls back to the status string when http_status is absent', () => {
			expect(isSuccessfulExecution(exec({ http_status: null, status: 'completed' }))).toBe(
				true,
			);
			expect(isSuccessfulExecution(exec({ http_status: null, status: 'failed' }))).toBe(
				false,
			);
		});

		it('treats running/error/empty status strings as not-successful', () => {
			expect(isSuccessfulExecution(exec({ http_status: null, status: 'running' }))).toBe(
				false,
			);
			expect(isSuccessfulExecution(exec({ http_status: null, status: 'error' }))).toBe(false);
			expect(isSuccessfulExecution(exec({ http_status: null, status: '' }))).toBe(false);
		});

		it('accepts succeeded/success status synonyms', () => {
			expect(isSuccessfulExecution(exec({ http_status: null, status: 'succeeded' }))).toBe(
				true,
			);
			expect(isSuccessfulExecution(exec({ http_status: null, status: 'success' }))).toBe(
				true,
			);
		});
	});

	describe('deriveSuccessRate', () => {
		it('returns null for an empty sample', () => {
			expect(deriveSuccessRate([])).toBeNull();
		});

		it('computes the ratio of successful executions', () => {
			const rate = deriveSuccessRate([
				exec({ http_status: 200 }),
				exec({ http_status: 500 }),
				exec({ http_status: 200 }),
				exec({ http_status: 200 }),
			]);
			expect(rate).toBe(0.75);
		});

		it('returns 0 when every execution failed', () => {
			expect(
				deriveSuccessRate([exec({ http_status: 500 }), exec({ http_status: 404 })]),
			).toBe(0);
		});

		it('returns 1 when every execution succeeded', () => {
			expect(
				deriveSuccessRate([exec({ http_status: 200 }), exec({ http_status: 201 })]),
			).toBe(1);
		});
	});

	describe('approxCountFromPage / formatApproxCount', () => {
		it('reports an exact count when the page is complete', () => {
			const count = approxCountFromPage({ data: [1, 2, 3], has_more: false });
			expect(count).toEqual({ value: 3, atLeast: false });
			expect(formatApproxCount(count)).toBe('3');
		});

		it('reports a floor ("N+") when more rows exist', () => {
			const count = approxCountFromPage({ data: [1, 2], has_more: true });
			expect(count).toEqual({ value: 2, atLeast: true });
			expect(formatApproxCount(count)).toBe('2+');
		});

		it('reports 0 for an empty page (fresh install)', () => {
			const count = approxCountFromPage({ data: [], has_more: false });
			expect(count).toEqual({ value: 0, atLeast: false });
			expect(formatApproxCount(count)).toBe('0');
		});

		it('reports "0+" for an empty-but-paginated page', () => {
			const count = approxCountFromPage({ data: [], has_more: true });
			expect(count).toEqual({ value: 0, atLeast: true });
			expect(formatApproxCount(count)).toBe('0+');
		});
	});
});
