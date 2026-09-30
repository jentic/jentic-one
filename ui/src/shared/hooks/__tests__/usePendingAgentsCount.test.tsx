import type { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { usePendingAgentsCount } from '@/shared/hooks/usePendingAgentsCount';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

/** Minimal wire-shaped pending row; `minutesAgo` sets how long it has waited. */
function wireRow(id: string, name: string, minutesAgo: number) {
	return {
		id,
		name,
		description: null,
		owner_id: null,
		registered_by: 'self',
		parent_agent_id: null,
		approved_by: null,
		status: 'pending',
		denial_reason: null,
		denied_by: null,
		created_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
		approved_at: null,
		has_api_key: false,
	};
}

/** One cursor page of the pending list (`has_more` derives from the cursor). */
function pageOf(rows: ReturnType<typeof wireRow>[], nextCursor: string | null) {
	return HttpResponse.json({
		data: rows,
		has_more: nextCursor !== null,
		next_cursor: nextCursor,
	});
}

describe('usePendingAgentsCount', () => {
	it('reports the count of pending agents from the real endpoint', async () => {
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBeGreaterThan(0));
		// The seed's pending set fits in one page, so the drain completes in
		// one round-trip: an exact count, not "N+".
		await waitFor(() => expect(result.current.complete).toBe(true));
		expect(result.current.atLeast).toBe(false);
	});

	it('returns the pending rows themselves in backend order (created_at DESC)', async () => {
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBeGreaterThan(0));

		const { agents, count } = result.current;
		// The rows back the approval banner; the count backs the nav badge —
		// one cache slice, so they can never disagree.
		expect(agents).toHaveLength(count);
		const times = agents.map((a) => Date.parse(a.created_at));
		expect(times).toEqual([...times].sort((a, b) => b - a));
		expect(agents[agents.length - 1]?.name).toBe('inbox-triage-bot');
	});

	it('drains every pending page — exact count, complete, the oldest agent LAST', async () => {
		const cursors: Array<string | null> = [];
		worker.use(
			http.get('/agents', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				cursors.push(cursor);
				// Three DESC pages continuing ONE created_at sequence — the
				// true longest-waiting agent lives on the LAST page, exactly
				// the row a first-page-only read could never surface.
				if (cursor === null) {
					return pageOf(
						[wireRow('agnt_pg1a', 'newest-bot', 2), wireRow('agnt_pg1b', 'p1-bot', 10)],
						'cur-2',
					);
				}
				if (cursor === 'cur-2') {
					return pageOf(
						[wireRow('agnt_pg2a', 'p2-bot', 25), wireRow('agnt_pg2b', 'p2b-bot', 40)],
						'cur-3',
					);
				}
				return pageOf(
					[
						wireRow('agnt_pg3a', 'p3-bot', 60),
						wireRow('agnt_pg3b', 'true-oldest-bot', 90),
					],
					null,
				);
			}),
		);
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.complete).toBe(true));

		// Whole list, so the count is exact — never hedged once complete.
		expect(result.current.count).toBe(6);
		expect(result.current.atLeast).toBe(false);
		// The flattened pages preserve the DESC sequence end to end, so the
		// LAST row is the true longest-waiting agent (from the last page).
		expect(result.current.agents[result.current.agents.length - 1]?.name).toBe(
			'true-oldest-bot',
		);
		// One eager drain: the first page then each cursor exactly once.
		expect(cursors).toEqual([null, 'cur-2', 'cur-3']);
	});

	it('reports a floor (atLeast, never complete) when a later page fails — and stops the drain', async () => {
		const cursors: Array<string | null> = [];
		worker.use(
			http.get('/agents', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				cursors.push(cursor);
				if (cursor === null) {
					return pageOf(
						[
							wireRow('agnt_fl1a', 'newest-bot', 2),
							wireRow('agnt_fl1b', 'loaded-oldest-bot', 30),
						],
						'cur-2',
					);
				}
				return HttpResponse.json({ detail: 'Server error' }, { status: 500 });
			}),
		);
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBe(2));

		// The loaded rows stay usable but are an honest floor, never whole.
		await waitFor(() => expect(result.current.atLeast).toBe(true));
		expect(result.current.complete).toBe(false);
		expect(result.current.agents[result.current.agents.length - 1]?.name).toBe(
			'loaded-oldest-bot',
		);

		// Guarded-drain regression (prior Bugbot finding): the error state
		// stops the eager drain — no refetch loop hammering the endpoint.
		const settled = cursors.length;
		await new Promise((resolve) => setTimeout(resolve, 300));
		expect(cursors.length).toBe(settled);
		expect(cursors).toEqual([null, 'cur-2']);
	});

	it('resolves to 0 (never a misleading badge) when the request fails', async () => {
		worker.use(createErrorHandler('get', '/agents', { status: 500 }));
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBe(0));
		// Nothing loaded reads as an honest 0 — not complete, but also never
		// "0+": a transient failure must not paint a phantom badge.
		expect(result.current.atLeast).toBe(false);
		expect(result.current.complete).toBe(false);
		expect(result.current.agents).toEqual([]);
	});
});
