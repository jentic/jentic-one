/**
 * "Execution Volume" card — ported from jentic-mini's ApiDailyBarChart look
 * (titled card, stacked daily bars, legend). No charting library: hand-rolled
 * bars, matching the rest of the module.
 *
 * Parity note: jentic-mini stacks each bar by API / toolkit / agent (a grouping
 * toggle) using a richer feed. The current endpoint only gives per-day
 * success/failed totals, so bars stack success (green) over failed (pink).
 * Grouping is tracked in jentic-one#561.
 */
import { useId } from 'react';
import type { DailyExecutionBucket, ExecutionStatsResponse } from '@/modules/monitor/api';

function formatDay(date: string): string {
	const parsed = new Date(`${date}T00:00:00Z`);
	if (Number.isNaN(parsed.getTime())) return date;
	return parsed.toLocaleDateString(undefined, { weekday: 'short', timeZone: 'UTC' });
}

function formatDate(date: string): string {
	const parsed = new Date(`${date}T00:00:00Z`);
	if (Number.isNaN(parsed.getTime())) return '';
	return parsed.toLocaleDateString(undefined, {
		month: 'numeric',
		day: 'numeric',
		timeZone: 'UTC',
	});
}

function DailyVolumeChart({ buckets }: { buckets: DailyExecutionBucket[] }) {
	const titleId = useId();
	const max = Math.max(1, ...buckets.map((b) => b.total));

	return (
		<ul aria-labelledby={titleId} className="mt-4 flex h-44 items-end justify-between gap-1.5">
			{buckets.map((b) => {
				const successPct = (b.success / max) * 100;
				const failedPct = (b.failed / max) * 100;
				return (
					<li
						key={b.date}
						className="group relative flex h-full flex-1 flex-col justify-end gap-1"
						aria-label={`${formatDay(b.date)} ${formatDate(b.date)}: ${b.total} executions, ${b.success} succeeded, ${b.failed} failed`}
					>
						<div className="flex h-full flex-col justify-end overflow-hidden rounded-md">
							<div
								className="bg-accent-pink w-full transition-all duration-500"
								style={{ height: `${failedPct}%` }}
							/>
							<div
								className="bg-accent-green w-full transition-all duration-500"
								style={{ height: `${successPct}%` }}
							/>
						</div>
						<div className="text-center leading-tight">
							<p className="text-foreground text-[10px] font-medium">
								{formatDay(b.date)}
							</p>
							<p className="text-muted-foreground text-[9px]">{formatDate(b.date)}</p>
						</div>

						{/* hover tooltip */}
						<div className="border-border/40 bg-card/95 pointer-events-none absolute bottom-full left-1/2 z-30 mb-1 hidden -translate-x-1/2 rounded-lg border px-2.5 py-1.5 text-[11px] whitespace-nowrap shadow-lg backdrop-blur-md group-hover:block">
							<p className="text-foreground font-semibold">{b.total} total</p>
							<p className="text-accent-green">{b.success} ok</p>
							{b.failed > 0 && <p className="text-accent-pink">{b.failed} failed</p>}
						</div>
					</li>
				);
			})}
		</ul>
	);
}

export function UsageCharts({ stats }: { stats: ExecutionStatsResponse }) {
	const titleId = useId();
	return (
		<div className="border-border bg-card rounded-xl border p-4">
			<div className="flex items-start justify-between">
				<div>
					<h2 id={titleId} className="text-foreground text-sm font-semibold">
						Execution Volume
					</h2>
					<p className="text-muted-foreground text-xs">
						Daily executions, success vs failed
					</p>
				</div>
				<div className="flex items-center gap-3 text-xs">
					<span className="text-muted-foreground flex items-center gap-1.5">
						<span className="bg-accent-green inline-block h-2 w-2 rounded-sm" />
						Success
					</span>
					<span className="text-muted-foreground flex items-center gap-1.5">
						<span className="bg-accent-pink inline-block h-2 w-2 rounded-sm" />
						Failed
					</span>
				</div>
			</div>
			{stats.daily_buckets.length === 0 ? (
				<p className="text-muted-foreground py-12 text-center text-sm">
					No execution data available
				</p>
			) : (
				<DailyVolumeChart buckets={stats.daily_buckets} />
			)}
		</div>
	);
}
