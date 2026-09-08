/**
 * TrendLineChart — a compact, labelled line chart for time-series metrics
 * (success rate over a window, average latency, …). Grown out of Monitor's
 * hand-rolled SVG charts under the library-first rule: the design system has
 * no external chart dependency, so the primitive lives here in `shared/ui`.
 *
 * Honest-chart defaults: callers pin the y domain where the metric has a
 * natural scale (e.g. `[0, 100]` for percentages) so the line isn't
 * auto-zoomed into drama; otherwise the domain is padded min/max of the data.
 *
 * Moving the pointer over the plot highlights the nearest data point with a
 * guide line + dot and an immediate tooltip carrying its exact value and
 * timestamp — the line alone only communicates shape, not numbers.
 *
 * Rendering: a fixed-viewBox SVG stretched to the container
 * (`preserveAspectRatio="none"`) with `vector-effect: non-scaling-stroke` so
 * the line keeps a crisp constant width at any size. Axis labels, the guide
 * and the dot are HTML (not SVG) so they never distort. The whole figure is
 * exposed to AT as one `role="img"` with a caller-provided summary label.
 */
import { useMemo, useState } from 'react';
import type { PointerEvent } from 'react';
import { cn } from '@/shared/lib/utils';
import { tooltipTranslateX } from '@/shared/ui/charts/StackedBarChart';

export interface TrendPoint {
	/** Unix seconds. */
	ts: number;
	value: number;
}

interface TrendLineChartProps {
	data: TrendPoint[];
	/** Format a y value for the axis labels (e.g. `(v) => `${v}%``). */
	formatValue: (value: number) => string;
	/** Format an x timestamp (unix seconds) for the start/end labels. */
	formatTs: (ts: number) => string;
	/** Pin the y domain (e.g. `[0, 100]` for rates). Defaults to padded data min/max. */
	yDomain?: [number, number];
	/** Chart body height in px (labels excluded). */
	height?: number;
	/** Stroke tone — the line inherits `currentColor` from this class. */
	colorClassName?: string;
	/** One-sentence summary announced to screen readers. */
	ariaLabel: string;
	className?: string;
}

const VIEW_W = 100;
const VIEW_H = 100;

export function TrendLineChart({
	data,
	formatValue,
	formatTs,
	yDomain,
	height = 96,
	colorClassName = 'text-primary',
	ariaLabel,
	className,
}: TrendLineChartProps) {
	const [hovered, setHovered] = useState<number | null>(null);

	const geometry = useMemo(() => {
		if (data.length < 2) return null;
		const values = data.map((p) => p.value);
		let [yMin, yMax] = yDomain ?? [Math.min(...values), Math.max(...values)];
		if (!yDomain && yMin === yMax) {
			// A flat series still needs a non-zero domain to render mid-chart.
			yMin -= 1;
			yMax += 1;
		}
		const ySpan = yMax - yMin || 1;
		const tsMin = data[0].ts;
		const tsSpan = data[data.length - 1].ts - tsMin || 1;
		const points = data.map((p) => ({
			xPct: ((p.ts - tsMin) / tsSpan) * VIEW_W,
			yPct: VIEW_H - ((p.value - yMin) / ySpan) * VIEW_H,
		}));
		return {
			path: `M ${points.map((p) => `${p.xPct},${p.yPct}`).join(' L ')}`,
			points,
			yMin,
			yMax,
		};
	}, [data, yDomain]);

	if (!geometry) {
		return (
			<div
				className={cn(
					'border-border/60 text-muted-foreground flex items-center justify-center rounded-md border border-dashed text-xs',
					className,
				)}
				style={{ height }}
			>
				Not enough data yet
			</div>
		);
	}

	/** Snap the pointer's x position to the nearest data point. */
	function onPointerMove(e: PointerEvent<HTMLDivElement>) {
		const rect = e.currentTarget.getBoundingClientRect();
		if (rect.width === 0 || !geometry) return;
		const xPct = ((e.clientX - rect.left) / rect.width) * VIEW_W;
		let nearest = 0;
		geometry.points.forEach((p, i) => {
			if (Math.abs(p.xPct - xPct) < Math.abs(geometry.points[nearest].xPct - xPct)) {
				nearest = i;
			}
		});
		setHovered(nearest);
	}

	const point = hovered === null ? undefined : geometry.points[hovered];

	return (
		<figure role="img" aria-label={ariaLabel} className={cn('m-0', className)}>
			<div className="flex gap-2">
				<div
					className="text-muted-foreground flex shrink-0 flex-col justify-between text-right font-mono text-[10px] leading-none tabular-nums"
					aria-hidden="true"
					style={{ height }}
				>
					<span>{formatValue(geometry.yMax)}</span>
					<span>{formatValue(geometry.yMin)}</span>
				</div>
				<div
					className="relative min-w-0 flex-1"
					style={{ height }}
					onPointerMove={onPointerMove}
					onPointerDown={onPointerMove}
					onPointerLeave={() => setHovered(null)}
				>
					{/* Horizontal gridlines at 0/25/50/75/100% of the domain. */}
					<div
						className="pointer-events-none absolute inset-0 flex flex-col justify-between"
						aria-hidden="true"
					>
						{[0, 1, 2, 3, 4].map((i) => (
							<div key={i} className="bg-border/50 h-px w-full" />
						))}
					</div>
					<svg
						viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
						preserveAspectRatio="none"
						className={cn('absolute inset-0 h-full w-full', colorClassName)}
						aria-hidden="true"
					>
						<path
							d={geometry.path}
							fill="none"
							stroke="currentColor"
							strokeWidth={2}
							strokeLinecap="round"
							strokeLinejoin="round"
							vectorEffect="non-scaling-stroke"
						/>
					</svg>
					{hovered !== null && point && (
						<>
							<div
								aria-hidden="true"
								className="bg-border pointer-events-none absolute inset-y-0 w-px"
								style={{ left: `${point.xPct}%` }}
							/>
							<div
								aria-hidden="true"
								className={cn(
									'ring-background pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-current ring-2',
									colorClassName,
								)}
								style={{ left: `${point.xPct}%`, top: `${point.yPct}%` }}
							/>
							<div
								role="tooltip"
								className="border-border bg-background pointer-events-none absolute top-0 z-10 rounded-md border px-2.5 py-1.5 whitespace-nowrap shadow-md"
								style={{
									left: `${point.xPct}%`,
									transform: `translateX(${tooltipTranslateX(point.xPct / VIEW_W)}) translateY(-33%)`,
								}}
							>
								<p className="text-foreground font-mono text-[10px] leading-tight font-semibold tabular-nums">
									{formatValue(data[hovered].value)}
								</p>
								<p className="text-muted-foreground mt-0.5 font-mono text-[10px] leading-tight tabular-nums">
									{formatTs(data[hovered].ts)}
								</p>
							</div>
						</>
					)}
				</div>
			</div>
			<div
				className="text-muted-foreground mt-1.5 flex justify-between pl-8 font-mono text-[10px] leading-none tabular-nums"
				aria-hidden="true"
			>
				<span>{formatTs(data[0].ts)}</span>
				<span>{formatTs(data[data.length - 1].ts)}</span>
			</div>
		</figure>
	);
}
