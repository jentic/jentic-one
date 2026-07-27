/**
 * "Execution Volume" card — ported from jentic-mini's ApiDailyBarChart look
 * (titled card, stacked bars, legend). No charting library: hand-rolled bars,
 * matching the rest of the module.
 *
 * Reads the enriched usage endpoint's time buckets (jentic-one-internal#561).
 * `bucket_seconds` adapts to the window — 60s (≤1h), 1h (≤24h), 6h (≤7d),
 * daily beyond — and the backend only returns buckets that contain rows
 * (plain GROUP BY, no zero-fill), so we zero-fill the series client-side to
 * keep the time axis contiguous. Bars stack success (green) over failed
 * (pink).
 */
import { useId, useMemo } from 'react';
import type { UsageBucket, UsageResponse } from '@/modules/monitor/api';

const DAY_SECONDS = 86_400;
const SIX_HOURS = 21_600;

function bucketDate(b: UsageBucket): Date {
	return new Date(b.ts * 1000);
}

/** Axis granularity, from the backend's bucket tier. */
type Granularity = 'time' | 'day-time' | 'day';

function granularityOf(bucketSeconds: number): Granularity {
	if (bucketSeconds >= DAY_SECONDS) return 'day';
	// 6h buckets span multiple days, so a bare time-of-day label is ambiguous.
	if (bucketSeconds >= SIX_HOURS) return 'day-time';
	return 'time';
}

function formatBucketLabel(b: UsageBucket, granularity: Granularity): string {
	const date = bucketDate(b);
	if (Number.isNaN(date.getTime())) return '';
	if (granularity === 'time') {
		return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	return date.toLocaleDateString(undefined, { weekday: 'short' });
}

function formatBucketSubLabel(b: UsageBucket, granularity: Granularity): string {
	const date = bucketDate(b);
	if (Number.isNaN(date.getTime())) return '';
	if (granularity === 'time') return '';
	if (granularity === 'day-time') {
		return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	return date.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' });
}

/**
 * Expand the backend's sparse buckets into a contiguous series over
 * [since, until), inserting zero buckets for empty slots. Bucket timestamps
 * are epoch-floored multiples of `bucket_seconds`, so the first slot starts
 * at floor(since); a defensive cap avoids rendering thousands of bars if the
 * server ever returns a surprising window/tier combination.
 */
function zeroFillBuckets(usage: UsageResponse): UsageBucket[] {
	const step = usage.bucket_seconds;
	if (!step || step <= 0 || usage.until <= usage.since) return usage.buckets;
	const first = Math.floor(usage.since / step) * step;
	const count = Math.ceil((usage.until - first) / step);
	if (count > 400) return usage.buckets;
	const byTs = new Map(usage.buckets.map((b) => [b.ts, b]));
	return Array.from({ length: count }, (_, i) => {
		const ts = first + i * step;
		return byTs.get(ts) ?? { ts, total: 0, success: 0, failed: 0, avg_ms: 0 };
	});
}

function VolumeChart({
	buckets,
	granularity,
	labelledBy,
}: {
	buckets: UsageBucket[];
	granularity: Granularity;
	labelledBy: string;
}) {
	const max = Math.max(1, ...buckets.map((b) => b.total));
	// With sub-day windows there can be dozens of buckets — label every nth
	// column so the axis stays readable.
	const labelStep = Math.max(1, Math.ceil(buckets.length / 10));

	return (
		<ul aria-labelledby={labelledBy} className="mt-4 flex h-44 items-end justify-between gap-1">
			{buckets.map((b, i) => {
				const successPct = (b.success / max) * 100;
				const failedPct = (b.failed / max) * 100;
				const label = formatBucketLabel(b, granularity);
				const subLabel = formatBucketSubLabel(b, granularity);
				const showLabel = i % labelStep === 0;
				return (
					<li
						key={b.ts}
						className="group relative flex h-full flex-1 flex-col justify-end gap-1"
						aria-label={`${label}${subLabel ? ` ${subLabel}` : ''}: ${b.total} executions, ${b.success} succeeded, ${b.failed} failed`}
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
						<div className="h-6 text-center leading-tight">
							{showLabel && (
								<>
									<p className="text-foreground text-[10px] font-medium">
										{label}
									</p>
									{subLabel && (
										<p className="text-muted-foreground text-[9px]">
											{subLabel}
										</p>
									)}
								</>
							)}
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

function cadenceLabel(bucketSeconds: number): string {
	if (bucketSeconds >= DAY_SECONDS) return 'Daily';
	if (bucketSeconds >= SIX_HOURS) return '6-hour';
	if (bucketSeconds >= 3600) return 'Hourly';
	return 'Per-minute';
}

export function UsageCharts({ usage }: { usage: UsageResponse }) {
	const titleId = useId();
	const granularity = granularityOf(usage.bucket_seconds);
	const filled = useMemo(() => zeroFillBuckets(usage), [usage]);
	return (
		<div className="border-border bg-card rounded-xl border p-4">
			<div className="flex items-start justify-between">
				<div>
					<h2 id={titleId} className="text-foreground text-sm font-semibold">
						Execution Volume
					</h2>
					<p className="text-muted-foreground text-xs">
						{cadenceLabel(usage.bucket_seconds)} executions, success vs failed
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
			{usage.buckets.length === 0 ? (
				<p className="text-muted-foreground py-12 text-center text-sm">
					No execution data available
				</p>
			) : (
				<VolumeChart buckets={filled} granularity={granularity} labelledBy={titleId} />
			)}
		</div>
	);
}
