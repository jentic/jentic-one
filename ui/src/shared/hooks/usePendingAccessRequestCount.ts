import { useQuery } from '@tanstack/react-query';
import { listAccessRequests } from '@/shared/lib';
import { sharedQueryKeys } from '@/shared/api/queryKeys';

/** Stable key so the nav badge + any other consumer share one cache slice.
 * Derived from the shared access-request root so a prefix invalidation of
 * `accessRequestsRoot` (any decision path) also refreshes this badge. */
export const pendingAccessRequestCountKey = [
	...sharedQueryKeys.accessRequestsRoot,
	'pending',
	'count',
] as const;

/**
 * The number of access requests still awaiting a human decision
 * (`GET /access-requests?status=pending`). Powers the persistent nav badge so
 * the "N waiting" signal is visible even when the Agent Rail is collapsed or
 * hidden (below `xl`). Polls on a modest interval so the badge stays roughly
 * live without a dedicated push channel.
 *
 * Returns a floor count when more than one page is pending (rendered "N+"),
 * mirroring the Dashboard card's approximate count. Failures resolve to 0 so a
 * transient error never paints a misleading badge.
 */
export function usePendingAccessRequestCount(): { count: number; atLeast: boolean } {
	const { data } = useQuery({
		queryKey: pendingAccessRequestCountKey,
		queryFn: () => listAccessRequests({ status: 'pending', limit: 50 }),
		staleTime: 30_000,
		refetchInterval: 60_000,
		refetchOnWindowFocus: true,
	});
	return { count: data?.data.length ?? 0, atLeast: data?.has_more ?? false };
}
