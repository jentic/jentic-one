import { useQuery } from '@tanstack/react-query';
import { countAccessRequests } from '@/shared/lib';
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
 * (`GET /access-requests/count?status=pending`). Powers the persistent nav
 * badge so the "N waiting" signal is visible even when the Agent Rail is
 * collapsed or hidden (below `xl`). Polls on a modest interval so the badge
 * stays roughly live without a dedicated push channel.
 *
 * The count is exact — a server-side COUNT with no page-size cap — so there
 * is no "N+" flooring anymore (the hook used to fetch a limit-50 page and
 * count its rows). Failures resolve to 0 so a transient error never paints a
 * misleading badge.
 */
export function usePendingAccessRequestCount(): { count: number } {
	const { data } = useQuery({
		queryKey: pendingAccessRequestCountKey,
		queryFn: () => countAccessRequests({ status: 'pending' }),
		staleTime: 30_000,
		refetchInterval: 60_000,
		refetchOnWindowFocus: true,
	});
	return { count: data ?? 0 };
}
