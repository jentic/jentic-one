/**
 * ApiUsageSummary — one API's 7-day call volume (calls, failed calls, trend
 * sparkline), in the three densities the Library shows it:
 *
 *   - `compact` — a trailing column in a dense list row (the docked panel)
 *   - `row`     — a one-line strip on a tile (the workspace `ApiCard`)
 *   - `large`   — the headline figure of a card (the API hub)
 *
 * Presentational only: callers resolve the figure first (a missing usage row
 * is "0 calls" only when the usage list is exhaustive — see `callsInWeek`) and
 * render nothing when it's unknown. Usage is grouped by `vendor/name`, so the
 * figure covers every version of the API — the copy says so.
 */
import { SparklineChart } from '@/shared/ui/charts/SparklineChart';
import { cn } from '@/shared/lib/utils';

export interface ApiUsageSummaryProps {
	/** Calls in the last 7 days (already resolved — never render an unknown as 0). */
	calls: number;
	failed?: number;
	/** Daily series; a sparkline shows once it has two points. */
	trend?: number[];
	size: 'compact' | 'row' | 'large';
	className?: string;
	testId?: string;
	/** Test id for the failed-calls figure (shown in every size when `failed > 0`). */
	failuresTestId?: string;
}

const TITLE = 'Calls in the last 7 days (all versions)';

function plural(n: number, noun: string): string {
	return `${n.toLocaleString()} ${noun}${n === 1 ? '' : 's'}`;
}

export function ApiUsageSummary({
	calls,
	failed = 0,
	trend = [],
	size,
	className,
	testId,
	failuresTestId,
}: ApiUsageSummaryProps) {
	const hasTrend = trend.length >= 2;

	if (size === 'compact') {
		return (
			<div
				className={cn('flex shrink-0 flex-col items-end', className)}
				title={TITLE}
				data-testid={testId}
			>
				{hasTrend ? (
					<SparklineChart data={trend} width={56} height={18} className="text-primary" />
				) : null}
				<span className="text-muted-foreground font-mono text-[10px]">
					{calls.toLocaleString()} calls
				</span>
				{failed > 0 && (
					<span
						className="text-danger font-mono text-[10px]"
						data-testid={failuresTestId}
					>
						{failed.toLocaleString()} failed
					</span>
				)}
			</div>
		);
	}

	if (size === 'row') {
		return (
			<div
				className={cn('flex h-5 items-center gap-2 text-xs', className)}
				title={TITLE}
				data-testid={testId}
			>
				<span className="text-muted-foreground">7d</span>
				<span className="text-foreground font-mono tabular-nums">
					{plural(calls, 'call')}
				</span>
				{failed > 0 && (
					<span
						className="text-danger font-mono tabular-nums"
						data-testid={failuresTestId}
					>
						{failed.toLocaleString()} failed
					</span>
				)}
				<span className="sr-only">in the last 7 days, across all versions</span>
				{hasTrend ? (
					<SparklineChart
						data={trend}
						width={72}
						height={18}
						className="text-primary ml-auto"
					/>
				) : null}
			</div>
		);
	}

	return (
		<div className={cn('flex items-end justify-between gap-4', className)} data-testid={testId}>
			<div>
				<p className="text-foreground font-mono text-2xl font-semibold">
					{calls.toLocaleString()}
				</p>
				<p className="text-muted-foreground text-xs">
					{failed > 0 ? (
						<span className="text-danger" data-testid={failuresTestId}>
							{failed.toLocaleString()} failed
						</span>
					) : calls > 0 ? (
						'no failures'
					) : (
						'no calls this week'
					)}
					{' · all versions'}
				</p>
			</div>
			{hasTrend ? (
				<SparklineChart data={trend} width={140} height={36} className="text-primary" />
			) : null}
		</div>
	);
}
