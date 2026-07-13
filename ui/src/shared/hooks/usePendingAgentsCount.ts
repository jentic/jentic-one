import { useQuery } from '@tanstack/react-query';
import { AgentsService, sharedQueryKeys } from '@/shared/api';

/** Stable key so the nav badge + any other consumer share one cache slice.
 * Derived from the shared agents root so a prefix invalidation of `agentsRoot`
 * (any approve/deny/create path) also refreshes this badge. */
export const pendingAgentsCountKey = [...sharedQueryKeys.agentsRoot, 'pending', 'count'] as const;

/**
 * The number of agents still awaiting approval (`GET /agents?status=pending`).
 * Powers the persistent nav badge on the Agents tab so the "N waiting" signal
 * is visible even when the Agent Rail is collapsed or hidden (below `xl`),
 * mirroring `usePendingAccessRequestCount` for access requests. Polls on a
 * modest interval so the badge stays roughly live without a dedicated push
 * channel; the Agents module's approve/deny/create mutations invalidate the
 * shared agents root for instant in-UI updates. See issue #652.
 *
 * Returns a floor count when more than one page is pending (rendered "N+").
 * Failures resolve to 0 so a transient error never paints a misleading badge.
 */
export function usePendingAgentsCount(): { count: number; atLeast: boolean } {
	const { data } = useQuery({
		queryKey: pendingAgentsCountKey,
		queryFn: () => AgentsService.listAgents({ status: 'pending', limit: 50 }),
		staleTime: 30_000,
		refetchInterval: 60_000,
		refetchOnWindowFocus: true,
	});
	return { count: data?.data.length ?? 0, atLeast: data?.has_more ?? false };
}
