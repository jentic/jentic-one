/**
 * Overview tab — the headline usage lens, ported from jentic-mini's Overview.
 *
 * Powered by `GET /monitoring/executions` (`useExecutionStats`, jentic-one#386):
 * HealthStrip (success-rate health pill), the Execution Volume chart, and a
 * Breakdown of the busiest operations, over a selectable trailing window.
 *
 * Full jentic-mini parity (latency pill, "N active now", per-agent / per-toolkit
 * grouping, bubble chart, per-row sparklines) is gated on a richer aggregation
 * endpoint — tracked in jentic-one#561. Those surfaces degrade gracefully here.
 */
import { BarChart3 } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { EmptyState, ErrorAlert, LoadingState, SegmentedToggle } from '@/shared/ui';
import { useExecutionStats } from '@/modules/monitor/api';
import { HealthStrip } from '@/modules/monitor/components/HealthStrip';
import { UsageCharts } from '@/modules/monitor/components/UsageCharts';
import { TopOperations } from '@/modules/monitor/components/TopOperations';

const WINDOW_OPTIONS = [
	{ value: '1', label: '24h' },
	{ value: '7', label: '7d' },
	{ value: '30', label: '30d' },
];

function parseDays(value: string | null): number {
	const n = Number(value);
	return n === 1 || n === 7 || n === 30 ? n : 7;
}

export function OverviewTab() {
	// Share the `?days` URL param with the global filter bar so the window stays
	// consistent when switching between Overview and the list tabs.
	const [searchParams, setSearchParams] = useSearchParams();
	const days = parseDays(searchParams.get('days'));
	const setDays = (value: number) =>
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set('days', String(value));
				return next;
			},
			{ replace: true },
		);
	const stats = useExecutionStats({ days });

	const data = stats.data;
	const isEmpty = !!data && data.total_executions === 0;

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-3">
				<h2 className="text-foreground text-sm font-semibold">Usage</h2>
				<SegmentedToggle
					options={WINDOW_OPTIONS}
					value={String(days)}
					onChange={(v) => setDays(Number(v))}
					ariaLabel="Stats window"
				/>
			</div>

			{stats.isLoading ? (
				<LoadingState />
			) : stats.isError ? (
				<ErrorAlert
					message={
						stats.error instanceof Error
							? stats.error
							: 'Failed to load usage statistics.'
					}
					onRetry={() => stats.refetch()}
					retrying={stats.isFetching}
				/>
			) : isEmpty || !data ? (
				<EmptyState
					icon={<BarChart3 className="h-8 w-8" />}
					title="No executions yet"
					description={`No API calls were recorded in the last ${days} days. Once agents start running operations, usage trends and the busiest operations will appear here.`}
				/>
			) : (
				<>
					<HealthStrip stats={data} />
					<UsageCharts stats={data} />
					{data.top_operations.length > 0 && (
						<TopOperations operations={data.top_operations} />
					)}
				</>
			)}
		</div>
	);
}
