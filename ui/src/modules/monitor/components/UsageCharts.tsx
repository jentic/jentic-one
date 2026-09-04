/**
 * "Execution Volume" card — built on the
 * enriched usage endpoint (jentic-one-internal#561): an SVG stacked bar chart
 * colored per entity, with the APIs / Toolkits / Agents grouping toggle, an
 * interactive legend (hovering a chip or segment dims the rest), y-axis
 * gridlines, and a per-segment hover tooltip.
 *
 * The per-entity series comes from the endpoint's
 * `top[].trend` — equal segments spanning exactly [since, until), one per
 * aggregate bucket tier — and buildBars re-buckets those segments into a
 * handful of display buckets (six 4h slices for 24h, one bar per calendar day
 * for 7d, six range slices for 30d). Rendering the raw segments directly
 * would draw 12 bars whose 7d labels straddled 8 calendar dates and
 * crammed the x-axis on mobile. Executions outside the top rows (or
 * unattributed) appear as a muted "Other" remainder derived from the
 * aggregate `buckets`, so bar heights always add up to the real totals.
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

const OTHER_COLOR = '#94a3b8';
const DAY_SECONDS = 86_400;
// A day-aligned 7d window can exceed 7·86400s across a DST change; anything up
// to this bound still renders (and is captioned) as a per-day week view.
const WEEK_WINDOW_MAX_SECONDS = 8 * DAY_SECONDS;
// Bar count for the range views (24h and 30d+). Mini's six slices keep every
// x-axis label legible at 375px; the week view instead draws one bar per day.
const RANGE_SLICES = 6;

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
	if (windowSeconds <= DAY_SECONDS) return 'Last 24 hours';
	if (windowSeconds <= WEEK_WINDOW_MAX_SECONDS) return 'Last 7 days';
	return 'Last 30 days';
}

/** Display bucket: the x-axis slot a bar renders into. */
interface BucketDef {
	startSec: number;
	endSec: number;
	label: string;
	subLabel: string;
}

// Mini's compact 24-hour "H:MM" — locale hour formats ("03:30 PM") are wide
// enough that six of them collide on a 375px viewport.
function timeLabel(sec: number): string {
	const d = new Date(sec * 1000);
	return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function dateLabel(sec: number): string {
	const d = new Date(sec * 1000);
	return `${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * Mini's getBucketDefs, driven by the response window: six time-range slices
 * for a day, one bar per local calendar day for a week (exactly 7 for the
 * day-aligned 7d window the Overview requests), six date-range slices for
 * anything wider. Capping the bar count at RANGE_SLICES is what keeps the
 * x-axis legible on mobile — 12 raw trend segments at ≥20px each overflowed
 * narrow screens.
 */
function buildBucketDefs(sinceSec: number, untilSec: number): BucketDef[] {
	const windowSeconds = untilSec - sinceSec;
	if (windowSeconds <= DAY_SECONDS) {
		const step = windowSeconds / RANGE_SLICES;
		return Array.from({ length: RANGE_SLICES }, (_, i) => {
			const start = sinceSec + i * step;
			return {
				startSec: start,
				endSec: start + step,
				label: timeLabel(start),
				subLabel: `–${timeLabel(start + step)}`,
			};
		});
	}
	if (windowSeconds <= WEEK_WINDOW_MAX_SECONDS) {
		const defs: BucketDef[] = [];
		// Walk local calendar days via Date arithmetic (DST-safe) from the day
		// containing `since` until the window is covered.
		const day = new Date(sinceSec * 1000);
		day.setHours(0, 0, 0, 0);
		while (day.getTime() / 1000 < untilSec) {
			const start = day.getTime() / 1000;
			const label = day.toLocaleDateString(undefined, { weekday: 'short' });
			const sub = dateLabel(start);
			day.setDate(day.getDate() + 1);
			defs.push({ startSec: start, endSec: day.getTime() / 1000, label, subLabel: sub });
		}
		return defs;
	}
	const step = windowSeconds / RANGE_SLICES;
	return Array.from({ length: RANGE_SLICES }, (_, i) => {
		const start = sinceSec + i * step;
		return {
			startSec: start,
			endSec: start + step,
			label: dateLabel(start),
			// Inclusive end date — the exclusive boundary is midnight of the
			// next day, so labelling it would show a date outside the window
			// (the last slice would read "–tomorrow").
			subLabel: `–${dateLabel(start + step - 1)}`,
		};
	});
}

/**
 * Display bucket owning the instant `sec`. Attribution is by start instant,
 * clamped into range, so totals stay conserved even when a source bucket
 * straddles a display boundary (e.g. UTC-floored aggregate buckets vs local
 * calendar days).
 */
function defIndexFor(sec: number, defs: BucketDef[]): number {
	for (let i = 0; i < defs.length; i++) {
		if (sec < defs[i].endSec) return i;
	}
	return defs.length - 1;
}

/**
 * Build the stacked bars for a lens: per-entity counts from `top[].trend`,
 * plus a muted "Other" remainder so each bar reaches the aggregate total.
 * Both series are re-bucketed into the display buckets by the start instant
 * of each source segment — the same conserving arithmetic the backend used
 * to build them, so the two series line up.
 */
function buildBars(usage: UsageResponse, rows: EntityUsageRow[], palette: string[]): Bar[] {
	const windowSeconds = usage.until - usage.since;
	if (windowSeconds <= 0) return [];
	const defs = buildBucketDefs(usage.since, usage.until);

	const aggregate = new Array<number>(defs.length).fill(0);
	for (const b of usage.buckets) {
		aggregate[defIndexFor(b.ts, defs)] += b.total;
	}

	// Per-display-bucket entity counts. Trend segments are equal divisions of
	// [since, until); segment i starts at since + i·(window / trend.length).
	const perDef = defs.map(() => new Map<string, number>());
	for (const row of rows) {
		const num = row.trend.length;
		if (num === 0) continue;
		const segmentSeconds = windowSeconds / num;
		row.trend.forEach((count, i) => {
			if (count <= 0) return;
			const bucket = perDef[defIndexFor(usage.since + i * segmentSeconds, defs)];
			bucket.set(row.id, (bucket.get(row.id) ?? 0) + count);
		});
	}

	return defs.map((def, i) => {
		const segments: BarSegment[] = [];
		let entityTotal = 0;
		rows.forEach((row, rowIdx) => {
			const count = perDef[i].get(row.id) ?? 0;
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
		return {
			startSec: def.startSec,
			label: def.label,
			subLabel: def.subLabel,
			total: entityTotal + other,
			segments,
		};
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
			<div className="flex flex-wrap items-start justify-between gap-x-2 gap-y-1.5 px-4 pt-3 pb-0">
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
