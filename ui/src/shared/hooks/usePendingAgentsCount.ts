import { useMemo } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { AgentsService, sharedQueryKeys } from '@/shared/api';
import type { AgentResponse } from '@/shared/api';
import { useEagerCursorDrain } from '@/shared/hooks/useEagerCursorDrain';

/** Stable key so the nav badge + any other consumer share one cache slice.
 * Derived from the shared agents root so a prefix invalidation of `agentsRoot`
 * (any approve/deny/create path) also refreshes this badge. */
export const pendingAgentsCountKey = [...sharedQueryKeys.agentsRoot, 'pending', 'count'] as const;

/**
 * The agents still awaiting approval (`GET /agents?status=pending`), drained
 * across every cursor page. Powers the persistent nav badge on the Agents tab
 * so the "N waiting" signal is visible even when the Agent Rail is collapsed
 * or hidden (below `xl`), mirroring `usePendingAccessRequestCount` for access
 * requests. Polls on a modest interval so the badge stays roughly live
 * without a dedicated push channel; the Agents module's approve/deny/create
 * mutations invalidate the shared agents root for instant in-UI updates. See
 * issue #652.
 *
 * An infinite query with the shared guarded eager drain
 * ({@link useEagerCursorDrain}): the approval banner names the LONGEST-waiting
 * pending agent, and with >1 page pending that agent lives on the LAST page —
 * a first-page-only read could never name it. The 60s poll refetches every
 * drained page in sequence (TanStack v5 refetches an infinite query
 * page-by-page); with more than one page pending that costs one extra
 * round-trip per page per minute, a rare state and the badge's only lifeline,
 * so it is accepted. A failed later page stops the drain (guarded — no
 * refetch loop) and leaves the loaded rows as an honest floor.
 *
 * `count` is exact once `complete`; until then it is a floor and `atLeast` is
 * true (rendered "N+" by the badge). Failures on the FIRST page resolve to
 * `count: 0, atLeast: false` so a transient error never paints a misleading
 * badge.
 *
 * Also exposes the pending rows themselves (`agents`, backend order:
 * `created_at DESC`, newest first — the cursor pages continue one DESC
 * sequence, so the flattened list preserves it) so the approval banner on the
 * flat Agents surface can name the longest-waiting agent without a second
 * request — the badge and the banner deliberately share this one cache slice.
 */
export function usePendingAgentsCount(): {
	count: number;
	atLeast: boolean;
	agents: AgentResponse[];
	/** True only when every pending page loaded — the list (and count) is whole. */
	complete: boolean;
} {
	const query = useInfiniteQuery({
		queryKey: pendingAgentsCountKey,
		queryFn: ({ pageParam }) =>
			AgentsService.listAgents({ status: 'pending', limit: 50, cursor: pageParam }),
		initialPageParam: null as string | null,
		getNextPageParam: (last) => (last.has_more ? (last.next_cursor ?? null) : null),
		staleTime: 30_000,
		refetchInterval: 60_000,
		refetchOnWindowFocus: true,
	});
	useEagerCursorDrain(query);

	const { data } = query;
	const agents = useMemo(() => data?.pages.flatMap((page) => page.data) ?? [], [data]);
	const complete = query.isSuccess && !query.hasNextPage;
	return {
		count: agents.length,
		// Only a floor while the drain is still running or a later page failed;
		// nothing loaded at all reads as an honest 0, never "0+".
		atLeast: agents.length > 0 && !complete,
		agents,
		complete,
	};
}
