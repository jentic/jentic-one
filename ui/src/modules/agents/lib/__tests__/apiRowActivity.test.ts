import { describe, it, expect } from 'vitest';
import type { ActorApiUsage, ActorExecutionEntity } from '@/modules/agents/api';
import { callsBeyondScan, rowActivity } from '@/modules/agents/lib/apiRowActivity';

const tile = { credentialId: 'cred_1', vendor: 'slack.com', apiName: 'default' };

function call(id: string, over: Partial<ActorExecutionEntity> = {}): ActorExecutionEntity {
	return {
		id,
		status: 'succeeded',
		credentialId: 'cred_1',
		credentialName: null,
		toolkitId: null,
		toolkitName: null,
		operationId: null,
		operationPath: '/x',
		operationMethod: 'get',
		durationMs: 10,
		httpStatus: 200,
		error: null,
		startedAt: '2026-10-01T10:00:00Z',
		api: { vendor: 'slack.com', name: 'default' },
		traceId: null,
		...over,
	};
}

function usage(total: number): Map<string, ActorApiUsage | null> {
	return new Map([
		[
			'slack.com/default',
			{
				p50Ms: null,
				p95Ms: null,
				byCredential: new Map([
					['cred_1', { total, success: total, failed: 0, avgMs: 0, trend: [] }],
				]),
			},
		],
	]);
}

describe('rowActivity', () => {
	it("picks the row's calls and records what the feed scanned", () => {
		const other = call('exe_2', { api: { vendor: 'github.com', name: 'default' } });
		const a = rowActivity(tile, usage(1), { items: [call('exe_1'), other], hasMore: true });
		expect(a.calls?.map((c) => c.id)).toEqual(['exe_1']);
		expect(a.scanned).toEqual({ count: 2, hasMore: true });
	});

	it('leaves the scan unknown while loading or gated', () => {
		expect(rowActivity(tile, usage(0), undefined).scanned).toBeNull();
		expect(rowActivity(tile, usage(0), null).scanned).toBeNull();
	});
});

describe('callsBeyondScan', () => {
	const elsewhere = Array.from({ length: 100 }, (_, i) =>
		call(`exe_${i}`, { api: { vendor: 'github.com', name: 'default' } }),
	);

	it('is true when the newest calls went elsewhere and older ones exist', () => {
		expect(
			callsBeyondScan(rowActivity(tile, usage(0), { items: elsewhere, hasMore: true })),
		).toBe(true);
	});

	it('is true when the 7-day rollup counts calls the feed never reached', () => {
		expect(
			callsBeyondScan(rowActivity(tile, usage(5), { items: elsewhere, hasMore: false })),
		).toBe(true);
	});

	it('is false when the scan reached back to the start and the rollup is zero', () => {
		expect(callsBeyondScan(rowActivity(tile, usage(0), { items: [], hasMore: false }))).toBe(
			false,
		);
	});

	it('is false once the row has a call to show', () => {
		expect(
			callsBeyondScan(rowActivity(tile, usage(5), { items: [call('exe_1')], hasMore: true })),
		).toBe(false);
	});
});
