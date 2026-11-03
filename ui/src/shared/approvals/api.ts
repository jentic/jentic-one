/**
 * Pending held executions — the live state behind the bell's held-call rows,
 * the rail's Inbox, the attention toast, the waiting card on an agent and the
 * pending-count badges. Reads `GET /executions/holds?state=pending`, which the
 * server scopes to the held executions the caller may see (the agents they
 * own, or every agent for an `org:admin`).
 *
 * Shared rather than in the Agents module because the bell, the rail and the
 * nav badge are shell chrome; one held call's detail lives under
 * `sharedQueryKeys.approvalsRoot` (see `hooks.ts`).
 */
import { useCallback, useMemo } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useNow } from '@/shared/app/rail/useNow';
import {
	HeldExecutionState,
	HeldExecutionsService,
	sharedQueryKeys,
	type HeldExecutionResponse,
} from '@/shared/api';
import { useCanDecideApprovals } from '@/shared/approvals/pendingApprovals';
import { normaliseHeldCall } from '@/shared/approvals/client';

/**
 * Under the attention root, so anything that refreshes the inbox (a decision
 * made in the review pop-up, an approval event on the live stream) refreshes
 * the badges, the Inbox and the waiting card with it.
 */
export const pendingApprovalsKey = [
	...sharedQueryKeys.attentionRoot,
	'execution-approvals',
] as const;

/** The list endpoint's largest page. */
const PAGE_LIMIT = 100;

/**
 * Pages read per refresh. The backend caps pending holds per agent, so a few
 * pages cover any realistic fleet; past this the count is a floor.
 */
const MAX_PAGES = 5;

/** Same cadence as the rest of the inbox: roughly live without a push channel. */
const REFETCH_MS = 45_000;

export interface PendingApprovals {
	/** Still decidable (pending and not past `expires_at`), oldest first. */
	approvals: HeldExecutionResponse[];
	/** True when more pending approvals exist than were read. */
	truncated: boolean;
}

/**
 * Drop a pending row whose window already lapsed: the expiry sweep records it
 * shortly, and until then nobody can decide it. An expiry the row doesn't
 * state can't have passed.
 */
function stillDecidable(approval: HeldExecutionResponse, now: number): boolean {
	const at = Date.parse(approval.expires_at);
	return Number.isNaN(at) || at > now;
}

const byCreated = (a: HeldExecutionResponse, b: HeldExecutionResponse) =>
	Date.parse(a.created_at) - Date.parse(b.created_at);

/** `rows` less any whose window has closed; `rows` itself when none has, so nothing re-renders for nothing. */
function keepDecidable(rows: HeldExecutionResponse[], now: number): HeldExecutionResponse[] {
	const kept = rows.filter((row) => stillDecidable(row, now));
	return kept.length === rows.length ? rows : kept;
}

export async function listPendingApprovals(): Promise<PendingApprovals> {
	const rows: HeldExecutionResponse[] = [];
	let cursor: string | null = null;
	let truncated = false;
	for (let page = 0; page < MAX_PAGES; page++) {
		const res = await HeldExecutionsService.listHeldExecutions({
			state: HeldExecutionState.PENDING,
			limit: PAGE_LIMIT,
			cursor,
		});
		rows.push(...res.data.map(normaliseHeldCall));
		if (!res.has_more || !res.next_cursor) break;
		cursor = res.next_cursor;
		truncated = page === MAX_PAGES - 1;
	}
	return {
		approvals: keepDecidable(rows, Date.now()).sort(byCreated),
		truncated,
	};
}

/**
 * The held calls waiting on the caller. Pass `enabled: false` until the
 * viewer is known (see `useCanDecideApprovals`): no request goes out.
 */
export function usePendingApprovals(options?: { enabled?: boolean }) {
	// Read against the clock, not only at fetch time: a call whose window closes
	// between two reads leaves every list and count at the next tick.
	const now = useNow();
	const select = useCallback(
		(data: PendingApprovals): PendingApprovals => {
			const approvals = keepDecidable(data.approvals, now);
			return approvals === data.approvals ? data : { ...data, approvals };
		},
		[now],
	);
	return useQuery<PendingApprovals>({
		queryKey: pendingApprovalsKey,
		queryFn: listPendingApprovals,
		enabled: options?.enabled ?? true,
		staleTime: 30_000,
		refetchInterval: REFETCH_MS,
		select,
	});
}

/**
 * Under the approvals root, which a decision and every approval event on the
 * stream refresh along with the waiting list, so a decided call moves from
 * Waiting to Decided at once. Not under the attention root: the agent and
 * OAuth events that refresh it would re-read every loaded page for nothing.
 */
export const decidedApprovalsKey = [...sharedQueryKeys.approvalsRoot, 'decided'] as const;

/** Rows per read of the decided history. */
const DECIDED_PAGE_LIMIT = 25;

interface DecidedPage {
	calls: HeldExecutionResponse[];
	nextCursor: string | null;
}

/**
 * A call that no longer waits: decided, expired or withdrawn. A pending row
 * past its window counts as expired, so it is never missing from both lists
 * while the expiry sweep catches up.
 */
const isSettled = (row: HeldExecutionResponse, now: number) =>
	row.state !== HeldExecutionState.PENDING || !stillDecidable(row, now);

/**
 * One page of the decided history, newest asked first. The list endpoint has
 * no "settled only" filter, so waiting rows are dropped here, and a read that
 * finds only waiting rows reads on (up to `MAX_PAGES`) rather than come back
 * empty with more to show.
 */
async function listDecidedPage(cursor: string | null): Promise<DecidedPage> {
	const calls: HeldExecutionResponse[] = [];
	let next = cursor;
	for (let page = 0; page < MAX_PAGES; page++) {
		const res = await HeldExecutionsService.listHeldExecutions({
			limit: DECIDED_PAGE_LIMIT,
			cursor: next,
		});
		const now = Date.now();
		calls.push(...res.data.map(normaliseHeldCall).filter((row) => isSettled(row, now)));
		next = res.has_more && res.next_cursor ? res.next_cursor : null;
		if (calls.length > 0 || !next) break;
	}
	return { calls, nextCursor: next };
}

/**
 * The held calls that no longer wait on anyone, newest asked first, for the
 * Inbox's Decided view: every one the viewer can see (their own agents', or
 * every agent's for an `org:admin`). Read only while `enabled`; "Show more"
 * is `fetchNextPage`.
 */
export function useDecidedApprovals(options?: { enabled?: boolean }) {
	const query = useInfiniteQuery({
		queryKey: decidedApprovalsKey,
		queryFn: ({ pageParam }) => listDecidedPage(pageParam),
		initialPageParam: null as string | null,
		getNextPageParam: (last) => last.nextCursor,
		enabled: options?.enabled ?? true,
		staleTime: 30_000,
	});
	const { data } = query;
	// Each call once: a call decided between two reads can sit on both pages.
	const calls = useMemo(() => {
		const seen = new Set<string>();
		return (data?.pages ?? [])
			.flatMap((page) => page.calls)
			.filter((call) => !seen.has(call.id) && !!seen.add(call.id));
	}, [data]);
	return { ...query, calls };
}

/**
 * One agent's waiting calls, read on their own. For the agent on screen while
 * the shared list is cut short (`truncated`): its calls may lie past the pages
 * that list reads. Under the same key root, so what refreshes the list
 * refreshes this.
 */
export function useAgentPendingApprovals(agentId: string | null | undefined, enabled: boolean) {
	// Against the same clock as the shared list, so the two never disagree.
	const now = useNow();
	const select = useCallback((rows: HeldExecutionResponse[]) => keepDecidable(rows, now), [now]);
	return useQuery({
		queryKey: [...pendingApprovalsKey, 'agent', agentId ?? ''] as const,
		queryFn: async (): Promise<HeldExecutionResponse[]> => {
			const res = await HeldExecutionsService.listHeldExecutions({
				state: HeldExecutionState.PENDING,
				agentId,
				limit: PAGE_LIMIT,
			});
			return keepDecidable(res.data.map(normaliseHeldCall), Date.now()).sort(byCreated);
		},
		enabled: enabled && !!agentId,
		staleTime: 30_000,
		refetchInterval: REFETCH_MS,
		select,
	});
}

/**
 * The pending-count badges' read: how many held calls wait on the viewer. Off
 * (zero, no request) until the viewer is known. Shares the one cache slice with
 * the bell, the Inbox and the waiting card, so every count agrees.
 */
export function usePendingApprovalsCount(): { count: number; atLeast: boolean } {
	const canDecide = useCanDecideApprovals();
	const { data } = usePendingApprovals({ enabled: canDecide });
	if (!canDecide || !data) return { count: 0, atLeast: false };
	return { count: data.approvals.length, atLeast: data.truncated };
}
