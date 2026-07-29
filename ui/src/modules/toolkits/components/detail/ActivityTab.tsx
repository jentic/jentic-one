import { Activity, ExternalLink, ListOrdered, Lock, TrendingUp } from 'lucide-react';
import { AppLink, TrendLineChart } from '@/shared/ui';
import { useToolkitExecutions, useToolkitUsage } from '@/modules/toolkits/api';
import type { ToolkitExecution } from '@/modules/toolkits/api/types';
import { DetailSection, EmptyRow } from '@/modules/toolkits/components/detail/shared';
import { timeAgo } from '@/modules/toolkits/lib/time';
import { ROUTES } from '@/shared/app/routes';

/**
 * Activity tab — what this toolkit has actually been doing: the 7-day
 * execution-volume chart (`GET /monitoring/usage?toolkit_id=…`) and a recent
 * executions feed (`GET /executions?toolkit_id=…`), with a deep-link into
 * Monitor's full filterable log carrying the toolkit filter.
 *
 * Both endpoints are admin-gated; the repository maps 401/403 to `null` and
 * this tab degrades to a single explanatory panel for non-admins.
 */

function formatBucketTs(ts: number): string {
	return new Date(ts * 1000).toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
	});
}

function formatDuration(ms: number | null): string {
	if (ms == null) return '—';
	if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
	return `${ms}ms`;
}

const STATUS_DOT_CLASS: Record<string, string> = {
	completed: 'bg-success',
	failed: 'bg-danger',
	pending: 'bg-warning',
};

function ExecutionRow({ row }: { row: ToolkitExecution }) {
	const dotClass = STATUS_DOT_CLASS[row.status] ?? 'bg-muted-foreground';
	const failed = row.status === 'failed';
	return (
		<div
			data-testid="toolkit-execution-row"
			className="bg-muted/30 border-border/60 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-2.5"
		>
			<span
				className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`}
				aria-hidden="true"
				title={row.status}
			/>
			<div className="min-w-0 flex-1 basis-48">
				<p className="text-foreground truncate font-mono text-xs">
					{row.operation_id ?? row.api_label ?? row.trace_id}
					{row.http_status != null && (
						<span
							className={failed ? 'text-danger ml-2' : 'text-muted-foreground ml-2'}
						>
							{row.http_status}
						</span>
					)}
				</p>
				{row.error && <p className="text-danger mt-0.5 truncate text-xs">{row.error}</p>}
			</div>
			<span className="text-muted-foreground shrink-0 font-mono text-xs">
				{row.actor_type}/{row.actor_id}
			</span>
			<span className="text-muted-foreground w-14 shrink-0 text-right font-mono text-xs">
				{formatDuration(row.duration_ms)}
			</span>
			<span className="text-muted-foreground w-20 shrink-0 text-right text-xs">
				{timeAgo(Date.parse(row.started_at))}
			</span>
		</div>
	);
}

export function ActivityTab({ toolkitId }: { toolkitId: string }) {
	const { data: usage, isLoading: usageLoading } = useToolkitUsage(toolkitId);
	const { data: executions, isLoading: executionsLoading } = useToolkitExecutions(toolkitId);

	const loading = usageLoading || executionsLoading;
	const adminBlocked = !loading && usage === null && executions === null;

	if (adminBlocked) {
		return (
			<DetailSection title="Activity" icon={<Activity className="h-4 w-4" />}>
				<EmptyRow icon={<Lock />}>
					Execution history is admin-only. Ask an org admin, or check the toolkit's
					entries in Monitor if you have access.
				</EmptyRow>
			</DetailSection>
		);
	}

	const buckets = usage?.buckets ?? [];
	const chartData = buckets.map((b) => ({ ts: b.ts, value: b.total }));
	const rows = executions ?? [];

	return (
		<div className="space-y-6">
			<DetailSection title="Execution volume · 7d" icon={<TrendingUp className="h-4 w-4" />}>
				{loading ? (
					<div className="bg-muted h-28 animate-pulse rounded-lg" aria-hidden="true" />
				) : chartData.length >= 2 ? (
					<TrendLineChart
						data={chartData}
						formatValue={(v) => v.toLocaleString()}
						formatTs={formatBucketTs}
						height={112}
						ariaLabel={`Execution volume for this toolkit over the last 7 days: ${
							usage?.stats.total ?? 0
						} total executions.`}
					/>
				) : (
					<EmptyRow icon={<Activity />}>
						No executions in the last 7 days. Volume appears here once agents start
						calling this toolkit.
					</EmptyRow>
				)}
			</DetailSection>

			<DetailSection
				title="Recent executions"
				icon={<ListOrdered className="h-4 w-4" />}
				trailing={
					<AppLink
						href={`${ROUTES.monitor}?tab=executions&toolkit_id=${toolkitId}`}
						className="text-primary inline-flex items-center gap-1 text-xs font-medium"
					>
						Open in Monitor <ExternalLink className="h-3 w-3" aria-hidden="true" />
					</AppLink>
				}
			>
				{loading ? (
					<div className="space-y-2" aria-hidden="true">
						<div className="bg-muted h-10 animate-pulse rounded-lg" />
						<div className="bg-muted h-10 animate-pulse rounded-lg" />
					</div>
				) : rows.length === 0 ? (
					<EmptyRow icon={<Activity />}>No recent executions for this toolkit.</EmptyRow>
				) : (
					rows.map((row) => <ExecutionRow key={row.execution_id} row={row} />)
				)}
			</DetailSection>
		</div>
	);
}
