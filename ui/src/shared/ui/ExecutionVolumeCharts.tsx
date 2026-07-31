/**
 * ExecutionVolumeCharts — the console-standard activity chart pair, mirroring
 * the dashboard's arrangement: a stacked succeeded/failed volume chart on the
 * left (2/3) and a success-rate trend line on the right (1/3). Toolkit,
 * agent and service-account detail consoles all render the same pair so
 * "activity" reads identically everywhere.
 *
 * Both charts are fed by the same `/monitoring/usage` bucket shape
 * (`ts` / `total` / `success` / `failed`); callers just forward their
 * module's bucket array and window metadata.
 */
import { Activity, ChartColumn, TrendingUp } from 'lucide-react';
import { DetailSection, EmptyRow } from '@/shared/ui/DetailSection';
import { StackedBarChart, type StackedBarDatum } from '@/shared/ui/charts/StackedBarChart';
import { TrendLineChart } from '@/shared/ui/charts/TrendLineChart';

export interface UsageChartBucket {
	/** Bucket start, unix seconds. */
	ts: number;
	total: number;
	success: number;
	failed: number;
}

export interface ExecutionVolumeChartsProps {
	buckets: UsageChartBucket[];
	/** Width of each bucket in seconds (drives x-axis label formatting). */
	bucketSeconds: number;
	/** Short window caption appended to the card titles. Defaults to `7d`. */
	windowLabel?: string;
	/** Loading skeletons instead of charts. */
	isLoading?: boolean;
	/** Copy for the shared empty state when there are no buckets at all. */
	emptyMessage?: string;
}

/**
 * Label a bucket timestamp for the x-axis: clock time for sub-day buckets,
 * month + day otherwise (the dashboard's bucket-label convention).
 */
function bucketLabel(ts: number, bucketSeconds: number): string {
	const date = new Date(ts * 1000);
	if (bucketSeconds < 86_400) {
		return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function Legend() {
	return (
		<div className="text-muted-foreground flex items-center gap-3 text-xs">
			<span className="inline-flex items-center gap-1.5">
				<span className="bg-accent-green/80 h-2 w-2 rounded-sm" aria-hidden="true" />
				Succeeded
			</span>
			<span className="inline-flex items-center gap-1.5">
				<span className="bg-danger/80 h-2 w-2 rounded-sm" aria-hidden="true" />
				Failed
			</span>
		</div>
	);
}

export function ExecutionVolumeCharts({
	buckets,
	bucketSeconds,
	windowLabel = '7d',
	isLoading = false,
	emptyMessage = 'No executions in this window yet. Volume appears here once calls start flowing.',
}: ExecutionVolumeChartsProps) {
	const total = buckets.reduce((sum, b) => sum + b.total, 0);
	const failed = buckets.reduce((sum, b) => sum + b.failed, 0);

	const bars: StackedBarDatum[] = buckets.map((b) => ({
		key: String(b.ts),
		label: bucketLabel(b.ts, bucketSeconds),
		segments: [
			{
				key: 'success',
				label: 'succeeded',
				value: b.success,
				colorClassName: 'bg-accent-green/80',
			},
			{ key: 'failed', label: 'failed', value: b.failed, colorClassName: 'bg-danger/80' },
		],
	}));

	const trendData = buckets
		.filter((b) => b.total > 0)
		.map((b) => ({ ts: b.ts, value: (b.success / b.total) * 100 }));

	if (!isLoading && buckets.length === 0) {
		return (
			<DetailSection
				title={`Execution volume · ${windowLabel}`}
				icon={<ChartColumn className="h-4 w-4" />}
			>
				<EmptyRow icon={<Activity />}>{emptyMessage}</EmptyRow>
			</DetailSection>
		);
	}

	return (
		<div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
			<DetailSection
				title={`Execution volume · ${windowLabel}`}
				icon={<ChartColumn className="h-4 w-4" />}
				trailing={<Legend />}
				className="lg:col-span-2"
			>
				{isLoading ? (
					<div className="bg-muted h-32 animate-pulse rounded-lg" aria-hidden="true" />
				) : (
					<StackedBarChart
						bars={bars}
						height={128}
						ariaLabel={`Execution volume over the last ${windowLabel}: ${total} total, ${failed} failed.`}
					/>
				)}
			</DetailSection>

			<DetailSection
				title={`Success rate · ${windowLabel}`}
				icon={<TrendingUp className="h-4 w-4" />}
			>
				{isLoading ? (
					<div className="bg-muted h-32 animate-pulse rounded-lg" aria-hidden="true" />
				) : trendData.length >= 2 ? (
					<TrendLineChart
						data={trendData}
						height={128}
						yDomain={[0, 100]}
						formatValue={(v) => `${Math.round(v)}%`}
						formatTs={(ts) => bucketLabel(ts, bucketSeconds)}
						colorClassName="text-accent-green"
						ariaLabel={`Success rate per bucket over the last ${windowLabel}.`}
					/>
				) : (
					<p className="text-muted-foreground py-6 text-center text-sm">
						Not enough data for a trend yet.
					</p>
				)}
			</DetailSection>
		</div>
	);
}
