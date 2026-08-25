import { describe, it, expect } from 'vitest';
import {
	deriveSuccessRate,
	isSuccessfulExecution,
	approxCountFromPage,
	formatApproxCount,
	usageToKpis,
	usageToSuccessRateSeries,
	usageToLatencySeries,
	usageToTopRows,
} from '@/modules/dashboard/api/types';
import type { ExecutionResponse, UsageResponse } from '@/shared/api';

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

	describe('usage derivations (GET /monitoring/usage)', () => {
		function usage(partial: Partial<UsageResponse> = {}): UsageResponse {
			return {
				since: 1_700_000_000,
				until: 1_700_086_400,
				bucket_seconds: 3_600,
				group_by: 'api',
				stats: {
					total: 200,
					success: 188,
					failed: 10,
					avg_ms: 433.2,
					p50_ms: 388,
					p95_ms: 1239.6,
					active_now: 3,
					pending: 2,
				},
				buckets: [
					{ ts: 1_700_000_000, total: 40, success: 38, failed: 2, avg_ms: 420 },
					{ ts: 1_700_003_600, total: 0, success: 0, failed: 0, avg_ms: 0 },
					{ ts: 1_700_007_200, total: 60, success: 45, failed: 15, avg_ms: 510 },
				],
				top: [],
				...partial,
			};
		}

		describe('usageToKpis', () => {
			it('maps the stats block into the four headline KPIs', () => {
				expect(usageToKpis(usage())).toEqual({
					total: 200,
					successRate: 0.94,
					p95Ms: 1240,
					activeNow: 3,
				});
			});

			it('nulls the rate and p95 on an empty window', () => {
				const kpis = usageToKpis(
					usage({
						stats: {
							total: 0,
							success: 0,
							failed: 0,
							avg_ms: 0,
							p50_ms: null,
							p95_ms: null,
							active_now: 0,
							pending: 0,
						},
					}),
				);
				expect(kpis).toEqual({ total: 0, successRate: null, p95Ms: null, activeNow: 0 });
			});
		});

		describe('usageToSuccessRateSeries / usageToLatencySeries', () => {
			it('derives per-bucket percentages and skips empty buckets', () => {
				expect(usageToSuccessRateSeries(usage())).toEqual([
					{ ts: 1_700_000_000, value: 95 },
					{ ts: 1_700_007_200, value: 75 },
				]);
			});

			it('carries avg_ms per bucket (avg, not p95 — buckets have no p95)', () => {
				expect(usageToLatencySeries(usage())).toEqual([
					{ ts: 1_700_000_000, value: 420 },
					{ ts: 1_700_007_200, value: 510 },
				]);
			});
		});

		describe('usageToTopRows', () => {
			it('formats labels per group and sorts busiest-first', () => {
				const rows = usageToTopRows(
					usage({
						group_by: 'api',
						top: [
							{
								key: 'github/github-api',
								label: 'github/github-api',
								total: 80,
								success: 72,
								failed: 6,
								avg_ms: 655.4,
								trend: [1, 2],
							},
							{
								key: 'stripe/stripe-api',
								label: 'stripe/stripe-api',
								total: 120,
								success: 116,
								failed: 4,
								avg_ms: 412,
								trend: [3, 4],
							},
						],
					}),
				);
				expect(rows.map((r) => r.label)).toEqual(['stripe-api', 'github-api']);
				expect(rows[0]).toMatchObject({
					id: 'stripe/stripe-api',
					total: 120,
					avgMs: 412,
					trend: [3, 4],
				});
				expect(rows[0].successRate).toBeCloseTo(116 / 120);
			});

			it('strips the actor_type prefix for agent rows', () => {
				const rows = usageToTopRows(
					usage({
						group_by: 'agent',
						top: [
							{
								key: 'agent/invoice-bot',
								label: 'agent/invoice-bot',
								total: 10,
								success: 10,
								failed: 0,
								avg_ms: 100,
								trend: [],
							},
						],
					}),
				);
				expect(rows[0].label).toBe('invoice-bot');
			});

			it('surfaces null keys as an explicit Unattributed bucket', () => {
				const rows = usageToTopRows(
					usage({
						group_by: 'toolkit',
						top: [
							{
								key: null as unknown as string,
								label: null as unknown as string,
								total: 5,
								success: 4,
								failed: 1,
								avg_ms: 90,
								trend: [],
							},
						],
					}),
				);
				expect(rows[0]).toMatchObject({ id: '__unattributed__', label: 'Unattributed' });
			});

			it('nulls the rate for an empty row instead of dividing by zero', () => {
				const rows = usageToTopRows(
					usage({
						group_by: 'toolkit',
						top: [
							{
								key: 'tk_idle',
								label: 'tk_idle',
								total: 0,
								success: 0,
								failed: 0,
								avg_ms: 0,
								trend: [],
							},
						],
					}),
				);
				expect(rows[0].successRate).toBeNull();
			});
		});
	});
});
