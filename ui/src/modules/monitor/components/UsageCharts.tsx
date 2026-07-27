/**
 * "Execution Volume" card — jentic-mini's ApiDailyBarChart, rebuilt on the
 * enriched usage endpoint (jentic-one-internal#561): an SVG stacked bar chart
 * colored per entity, with the APIs / Toolkits / Agents grouping toggle, an
 * interactive legend (hovering a chip or segment dims the rest), y-axis
 * gridlines, and a per-segment hover tooltip.
 *
 * Mini bucketed raw TimelinePoints client-side. Here the per-entity series
 * comes from the endpoint's `top[].trend` — 12 equal segments spanning
 * exactly [since, until) — so each of the 12 bars stacks one segment per top
 * entity. Executions outside the top rows (or unattributed) appear as a
 * muted "Other" remainder derived from the aggregate `buckets`, so bar
 * heights always add up to the real totals.
 *
 * Colors reuse the shared lens palettes indexed by busiest-first row order,
 * matching the bubble chart and Breakdown, so an entity keeps one color
 * across all three charts.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';
import { SegmentedToggle } from '@/shared/ui';
import type { UsageResponse } from '@/modules/monitor/api';
import { getInitials, lensPalette, textColor, type UsageLens } from '@/modules/monitor/lib/palette';
import type { EntityUsageRow } from '@/modules/monitor/lib/usage';

const TREND_POINTS = 12;
const OTHER_COLOR = '#94a3b8';

interface BarSegment {
	key: string;
	label: string;
	count: number;
	color: string;
	textColor: string;
}

interface Bar {
	startSec: number;
	label: string;
	subLabel: string;
	total: number;
	segments: BarSegment[];
}

const LENS_NOUNS: Record<UsageLens, string> = {
	apis: 'API',
	toolkits: 'toolkit',
	agents: 'agent',
};

function windowSubtitle(windowSeconds: number): string {
	if (windowSeconds <= 86_400) return 'Last 24 hours';
	if (windowSeconds <= 7 * 86_400) return 'Last 7 days';
	return 'Last 30 days';
}

/**
 * Label a trend segment by its start instant. Unlike the backend's UTC-floored
 * daily buckets, trend segments are real instants (since + i·step), so local
 * time is always the right formatting.
 */
function segmentLabels(startSec: number, windowSeconds: number): { label: string; sub: string } {
	const date = new Date(startSec * 1000);
	if (Number.isNaN(date.getTime())) return { label: '', sub: '' };
	if (windowSeconds <= 86_400) {
		return {
			label: date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
			sub: '',
		};
	}
	if (windowSeconds <= 7 * 86_400) {
		return {
			label: date.toLocaleDateString(undefined, { weekday: 'short' }),
			sub: date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
		};
	}
	return {
		label: date.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' }),
		sub: '',
	};
}

/**
 * Build the 12 stacked bars for a lens: per-entity counts from `top[].trend`,
 * plus a muted "Other" remainder so the bar reaches the aggregate total.
 * Aggregate buckets are re-bucketed into the same 12 segments by flooring
 * (ts - since) / segmentSeconds — same arithmetic the backend used to build
 * the trends, so the two series line up.
 */
function buildBars(usage: UsageResponse, rows: EntityUsageRow[], palette: string[]): Bar[] {
	const windowSeconds = usage.until - usage.since;
	if (windowSeconds <= 0) return [];
	const num = Math.max(1, rows.find((r) => r.trend.length > 0)?.trend.length ?? TREND_POINTS);
	const segmentSeconds = windowSeconds / num;

	const aggregate = new Array<number>(num).fill(0);
	for (const b of usage.buckets) {
		// A bucket's span can straddle a segment boundary (6h buckets vs 14h
		// segments); attributing by start instant keeps totals conserved.
		const idx = Math.min(
			num - 1,
			Math.max(0, Math.floor((b.ts - usage.since) / segmentSeconds)),
		);
		aggregate[idx] += b.total;
	}

	return Array.from({ length: num }, (_, i) => {
		const startSec = usage.since + i * segmentSeconds;
		const { label, sub } = segmentLabels(startSec, windowSeconds);
		const segments: BarSegment[] = [];
		let entityTotal = 0;
		rows.forEach((row, rowIdx) => {
			const count = row.trend[i] ?? 0;
			if (count <= 0) return;
			entityTotal += count;
			const color = palette[rowIdx % palette.length];
			segments.push({
				key: row.id,
				label: row.label,
				count,
				color,
				textColor: textColor(color),
			});
		});
		const other = Math.max(0, aggregate[i] - entityTotal);
		if (other > 0) {
			segments.push({
				key: '__other__',
				label: 'Other',
				count: other,
				color: OTHER_COLOR,
				textColor: textColor(OTHER_COLOR),
			});
		}
		segments.sort((a, b) => b.count - a.count);
		return { startSec, label, subLabel: sub, total: entityTotal + other, segments };
	});
}

interface TooltipState {
	barIndex: number;
	x: number;
}

interface UsageChartsProps {
	/** API-grouped response (also carries the aggregate buckets/window). */
	usage: UsageResponse;
	apis: EntityUsageRow[];
	toolkits: EntityUsageRow[];
	agents: EntityUsageRow[];
	className?: string;
}

export function UsageCharts({ usage, apis, toolkits, agents, className }: UsageChartsProps) {
	const containerRef = useRef<HTMLDivElement>(null);
	const [width, setWidth] = useState(700);
	const [lens, setLens] = useState<UsageLens>('apis');
	const [tooltip, setTooltip] = useState<TooltipState | null>(null);
	const [hoveredSegKey, setHoveredSegKey] = useState<string | null>(null);

	// Mini's approach: draw the SVG at native pixel size (measured via
	// ResizeObserver) rather than stretching a fixed viewBox — a scaled
	// viewBox inflates axis text and bar geometry with the container width.
	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const next = Math.max(300, entry.contentRect.width);
				setWidth((prev) => (prev === next ? prev : next));
			}
		});
		observer.observe(el);
		return () => observer.disconnect();
	}, []);

	const rows = lens === 'apis' ? apis : lens === 'toolkits' ? toolkits : agents;
	const palette = lensPalette(lens);
	const windowSeconds = usage.until - usage.since;

	const bars = useMemo(() => buildBars(usage, rows, palette), [usage, rows, palette]);

	// Legend: one chip per entity that appears anywhere in the window,
	// busiest-first (mirrors mini's allSegments reduction).
	const legend = useMemo(() => {
		const seen = new Map<string, BarSegment & { total: number }>();
		for (const bar of bars) {
			for (const seg of bar.segments) {
				const prev = seen.get(seg.key);
				if (prev) prev.total += seg.count;
				else seen.set(seg.key, { ...seg, total: seg.count });
			}
		}
		return [...seen.values()].sort((a, b) => b.total - a.total);
	}, [bars]);

	const rawMax = Math.max(...bars.map((b) => b.total), 1);
	const maxTotal = Math.ceil(rawMax * 1.15);

	// Mini's exact geometry: 320px plot area, same paddings, 20px min bar width.
	const PADDING = { top: 16, bottom: 40, left: 40, right: 16 };
	const BAR_HEIGHT = 320;
	const svgH = PADDING.top + BAR_HEIGHT + PADDING.bottom;
	const chartW = width - PADDING.left - PADDING.right;
	const barW = Math.max(20, chartW / bars.length - 8);
	const gap = bars.length > 1 ? (chartW - barW * bars.length) / (bars.length - 1) : 0;
	const toY = (count: number) => PADDING.top + BAR_HEIGHT - (count / maxTotal) * BAR_HEIGHT;
	const yTickStep = Math.max(1, Math.ceil(maxTotal / 4));
	const yTicks = Array.from({ length: 5 }, (_, i) => i * yTickStep);

	const hasData = bars.some((b) => b.total > 0);
	const tooltipBar = tooltip ? bars[tooltip.barIndex] : null;

	return (
		<div
			ref={containerRef}
			className={cn(
				'border-border bg-card relative min-w-0 overflow-hidden rounded-xl border',
				className,
			)}
		>
			<div className="flex items-start justify-between gap-2 px-4 pt-3 pb-0">
				<div>
					<h2 className="text-foreground text-sm font-semibold">Execution Volume</h2>
					<p className="text-muted-foreground text-xs">
						{windowSubtitle(windowSeconds)}, colored by {LENS_NOUNS[lens]}
					</p>
				</div>
				<SegmentedToggle
					options={[
						{ value: 'apis', label: 'APIs' },
						{ value: 'toolkits', label: 'Toolkits' },
						{ value: 'agents', label: 'Agents' },
					]}
					value={lens}
					onChange={setLens}
					ariaLabel="Volume chart grouping"
				/>
			</div>

			{!hasData ? (
				<p className="text-muted-foreground py-16 text-center text-sm">
					No execution data available
				</p>
			) : (
				<>
					<svg
						width={width}
						height={svgH}
						viewBox={`0 0 ${width} ${svgH}`}
						role="img"
						aria-label={`Execution volume stacked by ${LENS_NOUNS[lens]}`}
					>
						{yTicks.map((tick) => (
							<g key={tick}>
								<line
									x1={PADDING.left}
									y1={toY(tick)}
									x2={width - PADDING.right}
									y2={toY(tick)}
									stroke="currentColor"
									strokeOpacity={tick === 0 ? 0.12 : 0.05}
									strokeDasharray={tick === 0 ? undefined : '3 3'}
								/>
								<text
									x={PADDING.left - 8}
									y={toY(tick)}
									textAnchor="end"
									dominantBaseline="middle"
									fontSize={10}
									fill="currentColor"
									opacity={0.4}
									style={{ fontFamily: 'var(--font-sans, system-ui)' }}
								>
									{tick}
								</text>
							</g>
						))}

						<AnimatePresence mode="wait">
							<motion.g
								key={lens}
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
							>
								{bars.map((bar, i) => {
									const bx = PADDING.left + i * (barW + gap);
									let stackY = PADDING.top + BAR_HEIGHT;

									return (
										<g
											key={bar.startSec}
											onMouseEnter={() =>
												setTooltip({ barIndex: i, x: bx + barW / 2 })
											}
											onMouseLeave={() => {
												setTooltip(null);
												setHoveredSegKey(null);
											}}
										>
											<rect
												x={bx}
												y={PADDING.top}
												width={barW}
												height={BAR_HEIGHT}
												fill="transparent"
											/>

											{bar.segments.map((seg) => {
												const segH = (seg.count / maxTotal) * BAR_HEIGHT;
												stackY -= segH;
												const isDimmed =
													hoveredSegKey && hoveredSegKey !== seg.key;
												return (
													<rect
														key={seg.key}
														x={bx}
														y={stackY}
														width={barW}
														height={Math.max(1, segH)}
														rx={segH > 4 ? 2 : 0}
														fill={seg.color}
														opacity={isDimmed ? 0.2 : 1}
														className="transition-opacity duration-150"
														onMouseEnter={() =>
															setHoveredSegKey(seg.key)
														}
													/>
												);
											})}

											<text
												x={bx + barW / 2}
												y={PADDING.top + BAR_HEIGHT + 16}
												textAnchor="middle"
												fontSize={11}
												fontWeight={500}
												fill="currentColor"
												opacity={0.6}
												style={{
													fontFamily: 'var(--font-sans, system-ui)',
												}}
											>
												{bar.label}
											</text>
											{bar.subLabel && (
												<text
													x={bx + barW / 2}
													y={PADDING.top + BAR_HEIGHT + 30}
													textAnchor="middle"
													fontSize={9}
													fill="currentColor"
													opacity={0.35}
													style={{
														fontFamily: 'var(--font-sans, system-ui)',
													}}
												>
													{bar.subLabel}
												</text>
											)}
										</g>
									);
								})}
							</motion.g>
						</AnimatePresence>
					</svg>

					<AnimatePresence mode="wait">
						<motion.div
							key={lens}
							className="border-border flex flex-wrap items-center gap-3 border-t px-4 py-2.5"
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							transition={{ duration: 0.2 }}
						>
							{legend.map((seg) => (
								<button
									key={seg.key}
									type="button"
									className={cn(
										'flex items-center gap-1.5 rounded-full py-0.5 pr-2 pl-0.5 transition-opacity',
										hoveredSegKey && hoveredSegKey !== seg.key
											? 'opacity-30'
											: 'opacity-100',
									)}
									onMouseEnter={() => setHoveredSegKey(seg.key)}
									onMouseLeave={() => setHoveredSegKey(null)}
								>
									<span
										className="flex h-4 w-4 items-center justify-center rounded-full text-[6px] font-bold"
										style={{
											backgroundColor: seg.color,
											color: seg.textColor,
										}}
									>
										{getInitials(seg.label)}
									</span>
									<span className="text-foreground text-[11px]">{seg.label}</span>
								</button>
							))}
						</motion.div>
					</AnimatePresence>

					{tooltipBar && tooltipBar.total > 0 && (
						<BarTooltip bar={tooltipBar} x={tooltip!.x} containerWidth={width} />
					)}
				</>
			)}
		</div>
	);
}

function BarTooltip({ bar, x, containerWidth }: { bar: Bar; x: number; containerWidth: number }) {
	const isRight = x > containerWidth * 0.6;

	return (
		<div
			className="border-border bg-card pointer-events-none absolute top-14 z-30 w-52 rounded-lg border p-3 shadow-xl"
			style={{ left: isRight ? x - 220 : x + 20 }}
		>
			<div className="mb-2 flex items-center justify-between">
				<span className="text-foreground text-xs font-medium">
					{bar.label} {bar.subLabel}
				</span>
				<span className="text-muted-foreground text-xs">{bar.total} total</span>
			</div>

			<div className="space-y-1.5">
				{bar.segments.map((seg) => (
					<div key={seg.key} className="flex items-center gap-2">
						<span
							className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full text-[6px] font-bold"
							style={{ backgroundColor: seg.color, color: seg.textColor }}
						>
							{getInitials(seg.label)}
						</span>
						<span className="text-foreground flex-1 truncate text-xs">{seg.label}</span>
						<span className="text-foreground text-xs font-medium">{seg.count}</span>
					</div>
				))}
			</div>
		</div>
	);
}
