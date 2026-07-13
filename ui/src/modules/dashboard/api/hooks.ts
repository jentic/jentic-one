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
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import {
	fetchAccessRequestsPage,
	fetchActionableEvents,
	fetchCatalogSize,
	fetchPendingAccessRequests,
	fetchPendingAgents,
	fetchRecentExecutions,
} from '@/modules/dashboard/api/client';
import type { DashboardApiError } from '@/modules/dashboard/api/client';
import type {
	AlertsOverview,
	CatalogOverview,
	PendingAccessRequestsOverview,
	PendingAgentsOverview,
	RecentExecutionsOverview,
} from '@/modules/dashboard/api/types';
import type { AccessRequestPage } from '@/shared/lib';
import { sharedQueryKeys } from '@/shared/api';

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
