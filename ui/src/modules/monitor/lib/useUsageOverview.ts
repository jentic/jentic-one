/**
 * useUsageOverview — everything Monitor's Overview draws, from
 * `GET /monitoring/usage` (jentic-one-internal#561), org:admin.
 *
 * One query per grouping dimension (APIs / Agents) so every
 * lens toggle on the charts and the breakdown is instant; the buckets and the
 * overall stats are identical across groupings, so they're read off the
 * API-grouped response.
 *
 * The window and actor come from the page's filter bar (`?days`, `?actor_id`)
 * so the Overview agrees with the activity log beside it. The actor maps onto
 * the endpoint's `agent_id` (the backend filters it against `actor_id`).
 * "All" is a list-only window — the aggregate needs a bounded range — so a
 * carried-over `days=all` reads as 30d here.
 *
 * The aggregate re-polls every {@link AUTO_REFRESH_MS} while the tab is
 * visible; `refresh` forces one now and `updatedAt` says how fresh it is.
 */
import { useMemo } from 'react';
import {
	GroupBy,
	USAGE_TOP_LIMIT,
	useUsageStats,
	useUsageWindow,
	type UsageResponse,
} from '@/modules/monitor/api';
import {
	usageToEntityRows,
	usageToOverview,
	type EntityUsageRow,
	type UsageOverview,
} from '@/modules/monitor/lib/usage';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';

/**
 * Auto-refresh cadence. Matches the backend's usage-cache TTL (30s): polling
 * faster would only re-read the cached aggregate.
 */
export const AUTO_REFRESH_MS = 30_000;

export interface UsageOverviewState {
	/** The window in days the aggregate covers (1 | 7 | 30). */
	days: number;
	usage: UsageResponse | undefined;
	overview: UsageOverview | null;
	apis: EntityUsageRow[];
	agents: EntityUsageRow[];
	isLoading: boolean;
	isFetching: boolean;
	error: unknown;
	retry: () => void;
	/** Refetch every grouping now; resolves once all have settled. */
	refresh: () => Promise<void>;
	/** When the data on screen was fetched (epoch ms), 0 before the first. */
	updatedAt: number;
}

export function useUsageOverview({ enabled }: { enabled: boolean }): UsageOverviewState {
	const filters = useMonitorFilters();
	const days = filters.days ?? 30;

	// Window bounds from the shared coarse clock (see `useUsageWindow`), so
	// every `UsageCharts` screen agrees on the query key for the same window.
	const { since, until } = useUsageWindow(days);

	const base = { since, until, topLimit: USAGE_TOP_LIMIT, agentId: filters.actorId };
	const poll = { enabled, refetchInterval: enabled ? AUTO_REFRESH_MS : false } as const;
	const apiUsage = useUsageStats({ ...base, groupBy: GroupBy.API }, poll);
	const agentUsage = useUsageStats({ ...base, groupBy: GroupBy.AGENT }, poll);
	const queries = [apiUsage, agentUsage];

	const usage = apiUsage.data;
	const overview = useMemo(() => (usage ? usageToOverview(usage) : null), [usage]);
	const apis = useMemo(() => usageToEntityRows(apiUsage.data), [apiUsage.data]);
	const agents = useMemo(() => usageToEntityRows(agentUsage.data), [agentUsage.data]);

	return {
		days,
		usage,
		overview,
		apis,
		agents,
		isLoading: queries.some((q) => q.isLoading),
		isFetching: queries.some((q) => q.isFetching),
		error: queries.find((q) => q.isError && !q.data)?.error ?? null,
		retry: () => {
			for (const q of queries) void q.refetch();
		},
		refresh: async () => {
			await Promise.all(queries.map((q) => q.refetch()));
		},
		updatedAt: Math.min(...queries.map((q) => q.dataUpdatedAt)),
	};
}
