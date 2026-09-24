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
 * or hidden (below `xl`). Polls on a modest interval so the badge stays
 * roughly live without a dedicated push channel; the Agents module's
 * approve/deny/create mutations invalidate the shared agents root for instant
 * in-UI updates. See issue #652.
 *
 * Drained via {@link useEagerCursorDrain} because the approval banner — which
 * shares this one cache slice — names the LONGEST-waiting agent, and that agent
 * lives on the LAST page. `count` is exact once `complete`, a floor until then
 * ("N+"); a first-page failure resolves to `count: 0, atLeast: false`.
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
		// A floor only while draining or after a later page failed; nothing loaded is 0.
		atLeast: agents.length > 0 && !complete,
		agents,
		complete,
	};
}
