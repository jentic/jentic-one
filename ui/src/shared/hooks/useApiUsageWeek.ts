import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GroupBy, MonitoringService } from '@/shared/api';
import { useOptionalCurrentUser } from '@/shared/auth/AuthContext';
import { ORG_ADMIN } from '@/shared/auth/usePermission';

/**
 * Per-API call volume over the trailing 7 days — `GET /monitoring/usage
 * ?group_by=api` (org:admin), the same aggregate Monitor's Overview breaks
 * down. Each `top` row is keyed `vendor/name` (see
 * `monitoring_repo.grouped_top`) and carries the window's total / failed
 * counts plus a `trend` sparkline series.
 *
 * Shared because both the Library's docked workspace panel (discover) and the
 * API hub (workspace) show it, and neither may import the other or Monitor.
 *
 * Honesty rules the callers rely on:
 *   - `available` is false for non-admins (the read is skipped) and when the
 *     read fails — callers render nothing, never a zero.
 *   - `exhaustive` is true when the backend returned fewer rows than asked
 *     for, i.e. every API with traffic is present — only then does a missing
 *     key mean "no calls in 7 days" rather than "outside the top N".
 */
export interface ApiUsageRow {
	total: number;
	failed: number;
	trend: number[];
}

export interface ApiUsageWeek {
	available: boolean;
	exhaustive: boolean;
	/** Keyed `vendor/name` (the usage grouping has no version). */
	byApi: Map<string, ApiUsageRow>;
	isLoading: boolean;
}

const TOP_LIMIT = 50;
const WEEK_SECONDS = 7 * 86_400;
// Floor the window edge to 5 minutes so repeated reads hit the backend's own
// usage cache instead of asking for a new window every second.
const STEP_SECONDS = 300;

// A static key: the window is computed at fetch time, so a refetch rolls it
// forward while the previous answer stays on screen (no skeleton flash).
const API_USAGE_WEEK_KEY = ['api-usage-week', { top: TOP_LIMIT }] as const;

export function apiUsageKeyFor(ref: { vendor: string; name: string }): string {
	return `${ref.vendor}/${ref.name}`;
}

export function useApiUsageWeek(): ApiUsageWeek {
	const user = useOptionalCurrentUser();
	const isAdmin = user?.permissions?.includes(ORG_ADMIN) ?? false;
	const query = useQuery({
		queryKey: API_USAGE_WEEK_KEY,
		queryFn: () => {
			const untilSec = Math.floor(Date.now() / 1000 / STEP_SECONDS) * STEP_SECONDS;
			return MonitoringService.getUsageStats({
				since: untilSec - WEEK_SECONDS,
				until: untilSec,
				groupBy: GroupBy.API,
				topLimit: TOP_LIMIT,
			});
		},
		enabled: isAdmin,
		staleTime: STEP_SECONDS * 1000,
		retry: false,
	});

	return useMemo(() => {
		const byApi = new Map<string, ApiUsageRow>();
		for (const row of query.data?.top ?? []) {
			// Unattributed executions surface with a null key — skip them.
			if (!row.key) continue;
			byApi.set(row.key, { total: row.total, failed: row.failed, trend: row.trend });
		}
		return {
			available: isAdmin && query.isSuccess,
			exhaustive: query.isSuccess && (query.data?.top.length ?? 0) < TOP_LIMIT,
			byApi,
			isLoading: isAdmin && query.isPending,
		};
	}, [isAdmin, query.data, query.isSuccess, query.isPending]);
}
