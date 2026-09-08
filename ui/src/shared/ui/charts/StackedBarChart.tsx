/**
 * StackedBarChart — a compact stacked-column chart for bucketed counts
 * (execution volume split into success/failed, …). Grown out of Monitor's
 * hand-rolled volume chart under the library-first rule; like the other
 * chart primitives it has no external dependency and renders plain flex
 * columns (no SVG needed for bars — HTML keeps hover states and rounded
 * caps trivial).
 *
 * Hovering (or touching) a column shows an immediate custom tooltip with the
 * bucket's exact per-segment counts — a native `title` was too slow (~1s
 * delay) and invisible on touch, which read as "the tooltips don't work".
 *
 * Accessibility: the chart body is one `role="img"` with a caller-provided
 * summary; the tooltip is a redundant sighted-pointer affordance and stays
 * presentational.
 */
import { useMemo, useState } from 'react';
import { cn } from '@/shared/lib/utils';

export interface StackedBarSegment {
	key: string;
	label: string;
	value: number;
	/** Background tone for the segment, e.g. `bg-accent-green/80`. */
	colorClassName: string;
}

export interface StackedBarDatum {
	key: string;
	/** Short x-axis label (e.g. "Mon", "14:00"). */
	label: string;
	segments: StackedBarSegment[];
}

interface StackedBarChartProps {
	bars: StackedBarDatum[];
	/** Chart body height in px (x labels excluded). */
	height?: number;
	/** Format a count for the y labels / tooltip values. */
	formatValue?: (value: number) => string;
	/** One-sentence summary announced to screen readers. */
	ariaLabel: string;
	/** How many x labels to show at most (evenly thinned). */
	maxXLabels?: number;
	className?: string;
}

function barTotal(bar: StackedBarDatum): number {
	return bar.segments.reduce((sum, s) => sum + s.value, 0);
}

/**
 * Horizontal placement for a tooltip pinned to a fraction of the chart width:
 * centred on the anchor, except near the edges where centring would spill
 * out of the card.
 */
export function tooltipTranslateX(frac: number): string {
	if (frac < 0.2) return '0%';
	if (frac > 0.8) return '-100%';
	return '-50%';
}

export function StackedBarChart({
	bars,
	height = 128,
	formatValue = (v) => String(v),
	ariaLabel,
	maxXLabels = 8,
	className,
}: StackedBarChartProps) {
	const max = useMemo(() => Math.max(1, ...bars.map(barTotal)), [bars]);
	const [hovered, setHovered] = useState<number | null>(null);

	// Thin the x labels evenly so dense windows (e.g. 30 daily buckets)
	// don't collide; first and last always keep theirs.
	const labelEvery = Math.max(1, Math.ceil(bars.length / maxXLabels));

	if (bars.length === 0) {
		return (
			<div
				className={cn(
					'border-border/60 text-muted-foreground flex items-center justify-center rounded-md border border-dashed text-xs',
					className,
				)}
				style={{ height }}
			>
				No data in this window
			</div>
		);
	}

	const hoveredBar = hovered === null ? undefined : bars[hovered];
	const hoveredFrac = hovered === null ? 0 : (hovered + 0.5) / bars.length;

	return (
		<div className={cn('flex gap-2', className)}>
			<div
				className="text-muted-foreground flex shrink-0 flex-col justify-between text-right font-mono text-[10px] leading-none tabular-nums"
				aria-hidden="true"
				style={{ height }}
			>
				<span>{formatValue(max)}</span>
				<span>0</span>
			</div>
			<div className="min-w-0 flex-1">
				<div role="img" aria-label={ariaLabel} className="relative" style={{ height }}>
					<div
						className="pointer-events-none absolute inset-0 flex flex-col justify-between"
						aria-hidden="true"
					>
						{[0, 1, 2].map((i) => (
							<div key={i} className="bg-border/50 h-px w-full" />
						))}
					</div>
					<div
						className="absolute inset-0 flex items-end gap-[3px]"
						onPointerLeave={() => setHovered(null)}
					>
						{bars.map((bar, i) => (
							<div
								key={bar.key}
								data-bar={bar.key}
								onPointerEnter={() => setHovered(i)}
								className={cn(
									'flex h-full min-w-0 flex-1 flex-col-reverse justify-start overflow-hidden rounded-t-[3px] transition-opacity duration-100',
									hovered !== null && hovered !== i && 'opacity-50',
								)}
							>
								{bar.segments.map((segment) =>
									segment.value > 0 ? (
										<div
											key={segment.key}
											className={cn('w-full', segment.colorClassName)}
											style={{
												height: `${(segment.value / max) * 100}%`,
											}}
										/>
									) : null,
								)}
							</div>
						))}
					</div>
					{hoveredBar && (
						<div
							role="tooltip"
							className="border-border bg-background pointer-events-none absolute bottom-full z-10 mb-1 rounded-md border px-2.5 py-1.5 whitespace-nowrap shadow-md"
							style={{
								left: `${hoveredFrac * 100}%`,
								transform: `translateX(${tooltipTranslateX(hoveredFrac)})`,
							}}
						>
							<p className="text-foreground font-mono text-[10px] leading-tight font-semibold">
								{hoveredBar.label}
							</p>
							{hoveredBar.segments.map((segment) => (
								<p
									key={segment.key}
									className="text-muted-foreground mt-0.5 flex items-center gap-1.5 font-mono text-[10px] leading-tight tabular-nums"
								>
									<span
										className={cn(
											'h-1.5 w-1.5 shrink-0 rounded-full',
											segment.colorClassName,
										)}
									/>
									{formatValue(segment.value)} {segment.label}
								</p>
							))}
						</div>
					)}
				</div>
				<div
					className="mt-1.5 flex gap-[3px] font-mono text-[10px] leading-none"
					aria-hidden="true"
				>
					{bars.map((bar, i) => (
						<span
							key={bar.key}
							className="text-muted-foreground min-w-0 flex-1 truncate text-center tabular-nums"
						>
							{i % labelEvery === 0 || i === bars.length - 1 ? bar.label : ''}
						</span>
					))}
				</div>
			</div>
		</div>
	);
}
