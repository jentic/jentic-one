/**
 * The actor-executions read as the Agents page sees it: wire rows mapped to
 * entities, with the backend's untraced placeholder dropped so no surface
 * offers a trace link that opens nothing.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { fetchActorExecutions } from '@/modules/agents/api/client';

function row(id: string, trace_id: string | null) {
	return {
		execution_id: id,
		status: 'succeeded',
		started_at: '2026-10-01T10:00:00Z',
		trace_id,
	};
}

describe('fetchActorExecutions', () => {
	beforeEach(() => setToken('test-token'));

	it('keeps a real trace id and maps "unknown" or empty to null', async () => {
		worker.use(
			http.get('/executions', () =>
				HttpResponse.json({
					data: [row('exe_1', 'trace_real'), row('exe_2', 'unknown'), row('exe_3', '')],
					has_more: true,
					next_cursor: null,
				}),
			),
		);
		const res = await fetchActorExecutions('agnt_1', 100);
		expect(res?.items.map((i) => i.traceId)).toEqual(['trace_real', null, null]);
		expect(res?.hasMore).toBe(true);
	});
});
