/**
 * Dashboard service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for Dashboard views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced). Mirrors the
 * backend's Service layer.
 *
 * Each composed source gets its OWN hook (its own query, cache slice, and
 * error/loading state) so a single failing endpoint degrades only its widget —
 * the overview still renders the others. That isolation is the whole point of
 * composing client-side instead of behind one aggregate call.
 */
import { useInfiniteQuery, useQuery, keepPreviousData } from '@tanstack/react-query';
import {
	fetchAccessRequestsPage,
	fetchActionableEvents,
	fetchCatalogSize,
	fetchPendingAccessRequests,
	fetchPendingAgents,
	fetchRecentExecutions,
	fetchUsageOverview,
	fetchHasAgents,
} from '@/modules/dashboard/api/client';
import type { DashboardApiError } from '@/modules/dashboard/api/client';
import {
	RANGE_SECONDS,
	type AlertsOverview,
	type CatalogOverview,
	type DashboardRange,
	type PendingAccessRequestsOverview,
	type PendingAgentsOverview,
	type RecentExecutionsOverview,
} from '@/modules/dashboard/api/types';
import type { AccessRequestPage } from '@/shared/lib';
import { sharedQueryKeys, GroupBy, type UsageResponse } from '@/shared/api';

/** Stable query-key roots so callers/tests can target invalidation precisely.
 * `all` derives from the shared cross-module registry so sibling modules (e.g.
 * Agents on approve/deny) and this factory can't drift (#511). */
export const dashboardKeys = {
	all: sharedQueryKeys.dashboardRoot,
	/** The shared access-request root (durable queue + nav badge live under it).
	 * Re-exposed here so Dashboard views invalidate it through their own api
	 * layer instead of importing `@/shared/api` directly (view-layer boundary). */
	accessRequestsRoot: sharedQueryKeys.accessRequestsRoot,
	pendingAgents: () => [...dashboardKeys.all, 'pending-agents'] as const,
	pendingAccessRequests: () => [...dashboardKeys.all, 'pending-access-requests'] as const,
	accessRequestsQueue: (status: string) =>
		[...dashboardKeys.all, 'access-requests-queue', status] as const,
	alerts: () => [...dashboardKeys.all, 'alerts'] as const,
	executions: () => [...dashboardKeys.all, 'recent-executions'] as const,
	catalog: () => [...dashboardKeys.all, 'catalog-size'] as const,
	hasAgents: () => [...dashboardKeys.all, 'has-agents'] as const,
	usage: (range: DashboardRange, lens: GroupBy) =>
		[...dashboardKeys.all, 'usage', range, lens] as const,
};

/**
 * The overview is "at a glance", not real-time: a 30s freshness window keeps
 * the four widgets from refetching on every mount/focus while still feeling
 * live. Set explicitly (rather than inheriting the global default) so the
 * intent is visible at the call site; the manual Refresh button invalidates
 * `dashboardKeys.all` for an on-demand update.
 */
const OVERVIEW_STALE_TIME = 30_000;

/**
 * Belt-and-suspenders polling for the overview. Most updates are now push: the
 * SSE→query bridge in `agentStream` refreshes the access-request surfaces the
 * instant an event lands; the in-dashboard decision paths invalidate on every
 * decision; and the Agents module's approve/deny/create mutations now invalidate
 * the shared `dashboardRoot` (via `sharedQueryKeys`), so the pending-agents tile
 * updates instantly when a decision is made inside this UI. The one case with no
 * push channel is a pending agent that arrives entirely out-of-band (created by
 * another operator / the backend, with no `agent.*` SSE event for the dashboard
 * to listen to). A modest background refetch catches that within ≤45s. Paused
 * while the tab is hidden (TanStack default) so it costs nothing in the
 * background.
 */
const OVERVIEW_REFETCH_INTERVAL = 45_000;

/** Agents awaiting approval (`GET /agents?status=pending`). */
export function usePendingAgents() {
	return useQuery<PendingAgentsOverview, DashboardApiError>({
		queryKey: dashboardKeys.pendingAgents(),
		queryFn: fetchPendingAgents,
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}

/** Pending access requests — the durable approval queue (`GET /access-requests?status=pending`). */
export function usePendingAccessRequests() {
	return useQuery<PendingAccessRequestsOverview, DashboardApiError>({
		queryKey: dashboardKeys.pendingAccessRequests(),
		queryFn: fetchPendingAccessRequests,
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}

/**
 * The full access-request queue for the `/app/access-requests` subpage,
 * cursor-paginated via "Load more". Defaults to `status=pending` (the actionable
 * queue) but accepts any status filter. Separate cache slice from the card's
 * overview hook so the two don't fight over the same key.
 */
export function useAccessRequestsQueue(status: string = 'pending') {
	return useInfiniteQuery<AccessRequestPage, DashboardApiError>({
		queryKey: dashboardKeys.accessRequestsQueue(status),
		queryFn: ({ pageParam }) =>
			fetchAccessRequestsPage({ status, cursor: (pageParam as string | null) ?? null }),
		initialPageParam: null as string | null,
		getNextPageParam: (lastPage) => (lastPage.has_more ? (lastPage.next_cursor ?? null) : null),
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}

/** Events that need a human (`GET /events?requires_action=true`). */
export function useActionableEvents() {
	return useQuery<AlertsOverview, DashboardApiError>({
		queryKey: dashboardKeys.alerts(),
		queryFn: fetchActionableEvents,
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}

/** Recent executions + derived success rate (`GET /executions`). */
export function useRecentExecutions() {
	return useQuery<RecentExecutionsOverview, DashboardApiError>({
		queryKey: dashboardKeys.executions(),
		queryFn: fetchRecentExecutions,
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}

/** Approximate API catalog size (`GET /apis`). */
export function useCatalogSize() {
	return useQuery<CatalogOverview, DashboardApiError>({
		queryKey: dashboardKeys.catalog(),
		queryFn: fetchCatalogSize,
		staleTime: OVERVIEW_STALE_TIME,
	});
}

/**
 * "Does this workspace have any agents at all?" — the first-run switch input
 * (`GET /agents?limit=1`, no status filter). Together with the recent-
 * executions sample this decides whether the landing page shows the working
 * dashboard or the setup checklist. Longer stale time: flipping from
 * first-run to working happens once per workspace lifetime, and the pending
 * queue (registered but unapproved agents) already flips it live because a
 * pending agent IS an agent.
 */
export function useHasAgents() {
	return useQuery<boolean, DashboardApiError>({
		queryKey: dashboardKeys.hasAgents(),
		queryFn: fetchHasAgents,
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}

/**
 * Real gateway aggregates for the health + usage layers
 * (`GET /monitoring/usage`, org:admin).
 *
 * Permission-aware via the caller: the endpoint 403s for non-admins, so the
 * consuming view passes `enabled: usePermission(ORG_ADMIN)` and hides the
 * section rather than firing a doomed request. The gate lives in the view
 * tier (not here) so this service tier never imports `@/shared/auth` — that
 * barrel reaches `@/shared/app/routes`, which imports this module's pages,
 * and the resulting cycle would evaluate components before `dashboardKeys`
 * initializes (TDZ).
 *
 * The query key carries the range + lens tokens (not raw timestamps — those
 * would mint a new cache slice every refetch); the actual window bounds are
 * computed at fetch time, ceiled to the NEXT minute so (a) refetches within
 * the same minute are cache-coherent server-side, (b) the window width stays
 * exactly the range (the backend picks its bucket tier from `until - since`,
 * so a server-defaulted `until` would tip a 24h window into the 6h tier),
 * and (c) the current partial minute is included — the aggregate must never
 * trail the executions feed (#913).
 *
 * `placeholderData: keepPreviousData` — flipping range/lens re-keys the query;
 * keeping the previous aggregate on screen (with `isFetching` signalling the
 * swap) stops the whole section collapsing to skeletons on every toggle.
 */
export function useUsageOverview(
	range: DashboardRange,
	lens: GroupBy = GroupBy.API,
	{ enabled = true }: { enabled?: boolean } = {},
) {
	return useQuery<UsageResponse, DashboardApiError>({
		queryKey: dashboardKeys.usage(range, lens),
		queryFn: () => {
			const until = (Math.floor(Date.now() / 60_000) + 1) * 60;
			return fetchUsageOverview({
				since: until - RANGE_SECONDS[range],
				until,
				groupBy: lens,
			});
		},
		enabled,
		placeholderData: keepPreviousData,
		staleTime: OVERVIEW_STALE_TIME,
		refetchInterval: OVERVIEW_REFETCH_INTERVAL,
	});
}
