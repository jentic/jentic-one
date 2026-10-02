/**
 * StatStrip — the one-line answer above Monitor's Overview: how much, how
 * well, how fast, what broke, and across which APIs.
 *
 *   Calls      count for the window + an area sparkline of the volume
 *   Success    rate, coloured by health tier, with a 3-bar tier meter
 *   Latency    p95 (falls back to the average), speed tier, p50 beneath
 *   Failed     count — links to the failed API calls when there are any
 *   APIs       "N active" with the busiest APIs' initials tiles
 *
 * One card with divided cells rather than four KPI cards: the numbers read as
 * one sentence and the charts below keep the visual weight. On phones Calls
 * heads the card with its sparkline and the other four sit in an even 2×2;
 * the tier words move under the numbers so nothing crowds a half-width cell. Values count up
 * on arrival and whenever the window changes, so a new slice visibly lands.
 */
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { animate, motion, useReducedMotion } from 'framer-motion';
import { ArrowUpRight } from 'lucide-react';
import { AppLink, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	HEALTH_LABEL,
	HEALTH_TEXT_CLASS,
	LATENCY_TEXT_CLASS,
	healthTier,
	latencyTier,
	type HealthTier,
	type LatencyTier,
} from '@/shared/lib/usageThresholds';
import type { UsageResponse } from '@/modules/monitor/api';
import { formatLatency } from '@/modules/monitor/lib/format';
import { API_PALETTE, getInitials, textColor } from '@/modules/monitor/lib/palette';
import type { EntityUsageRow, UsageOverview } from '@/modules/monitor/lib/usage';

const SPEED_LABEL: Record<LatencyTier, string> = { fast: 'Fast', normal: 'Normal', slow: 'Slow' };

const TIER_BAR: Record<HealthTier | LatencyTier, string> = {
	healthy: 'bg-accent-green',
	fast: 'bg-accent-green',
	degraded: 'bg-accent-orange',
	normal: 'bg-accent-orange',
	failing: 'bg-accent-pink',
	slow: 'bg-accent-pink',
};

/** How many of the meter's three bars light up, best tier first. */
const TIER_LEVEL: Record<HealthTier | LatencyTier, number> = {
	healthy: 3,
	fast: 3,
	degraded: 2,
	normal: 2,
	failing: 1,
	slow: 1,
};

const cellVariant = {
	hidden: { opacity: 0, y: 6 },
	show: {
		opacity: 1,
		y: 0,
		transition: { type: 'spring' as const, stiffness: 320, damping: 26 },
	},
};

/** Half-width cells on phones get a step smaller so values never wrap. */
const VALUE_CLASS = 'text-xl font-semibold sm:text-2xl';

/** The tier word, shown under the value on phones (beside the meter above). */
function TierWord({ className, children }: { className: string; children: ReactNode }) {
	return (
		<span className={cn('font-medium sm:hidden', className)}>
			{children}
			<span aria-hidden="true"> · </span>
		</span>
	);
}

export interface StatStripProps {
	overview: UsageOverview;
	usage: UsageResponse;
	apis: EntityUsageRow[];
	windowLabel: string;
	/** Drill-down into the window's API calls. */
	callsHref: string;
	/** Drill-down into the window's failed API calls. */
	failedHref: string;
}

export function StatStrip({
	overview,
	usage,
	apis,
	windowLabel,
	callsHref,
	failedHref,
}: StatStripProps) {
	const health = healthTier(overview.successRate);
	const latencyMs = overview.p95Ms ?? overview.avgLatencyMs;
	const speed = latencyTier(latencyMs);
	const failures = overview.failureCount;
	// Count before slicing: the tiles are capped, the number isn't.
	const activeApis = apis.filter((a) => a.totalExecutions > 0);
	const volume = usage.buckets.map((b) => b.total ?? 0);

	return (
		<motion.section
			aria-label="Usage at a glance"
			className="border-border bg-card grid grid-cols-2 overflow-hidden rounded-xl border sm:grid-cols-3 lg:grid-cols-5"
			initial="hidden"
			animate="show"
			variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}
		>
			<Cell
				label="Calls"
				hint={`last ${windowLabel}`}
				href={callsHref}
				className="col-span-2 sm:col-span-1"
			>
				<div className="flex items-end justify-between gap-3">
					<CountUp
						value={overview.totalExecutions}
						className="text-foreground text-2xl font-semibold"
					/>
					<AreaSpark
						data={volume}
						className="text-primary mb-1 h-8 w-32 max-w-[45%] sm:h-7 sm:w-24"
					/>
				</div>
			</Cell>

			<Cell
				label="Success rate"
				hint={
					<>
						<TierWord className={HEALTH_TEXT_CLASS[health]}>
							{HEALTH_LABEL[health]}
						</TierWord>
						{overview.successCount.toLocaleString()} succeeded
					</>
				}
			>
				<div className="flex items-end gap-2.5">
					<CountUp
						value={overview.successRate}
						decimals={1}
						suffix="%"
						className={cn(VALUE_CLASS, HEALTH_TEXT_CLASS[health])}
					/>
					<TierMeter tier={health} label={HEALTH_LABEL[health]} />
				</div>
			</Cell>

			<Cell
				label={overview.p95Ms != null ? 'p95 latency' : 'Avg latency'}
				hint={
					<>
						<TierWord className={LATENCY_TEXT_CLASS[speed]}>
							{SPEED_LABEL[speed]}
						</TierWord>
						{overview.p50Ms != null ? `p50 ${formatLatency(overview.p50Ms)}` : null}
					</>
				}
			>
				<div className="flex items-end gap-2.5">
					<span className={cn(VALUE_CLASS, 'tabular-nums', LATENCY_TEXT_CLASS[speed])}>
						{formatLatency(latencyMs)}
					</span>
					<TierMeter tier={speed} label={SPEED_LABEL[speed]} />
				</div>
			</Cell>

			<Cell
				label="Failed"
				hint={failures > 0 ? 'view failed calls' : 'none in window'}
				href={failures > 0 ? failedHref : undefined}
			>
				<CountUp
					value={failures}
					className={cn(VALUE_CLASS, failures > 0 ? 'text-danger' : 'text-foreground')}
				/>
			</Cell>

			<Cell
				label="APIs active"
				hint={activeApis[0] ? `busiest: ${activeApis[0].label}` : undefined}
				className="sm:col-span-2 lg:col-span-1"
			>
				<div className="flex items-end gap-3">
					<CountUp
						value={activeApis.length}
						className={cn(VALUE_CLASS, 'text-foreground')}
					/>
					<ApiTiles
						apis={activeApis.slice(0, 5)}
						extra={Math.max(0, activeApis.length - 5)}
					/>
				</div>
			</Cell>
		</motion.section>
	);
}

/** The five cells' spans, shared by the strip and its placeholder. */
const CELL_SPANS = ['col-span-2 sm:col-span-1', '', '', '', 'sm:col-span-2 lg:col-span-1'];

/**
 * The strip's shape while usage loads. Same grid, same line boxes, so the
 * charts and the Live activity panel below are already where they'll stay —
 * the stream morphing in from the rail lands in its final place.
 */
export function StatStripSkeleton() {
	return (
		<section
			aria-hidden="true"
			className="border-border bg-card grid grid-cols-2 overflow-hidden rounded-xl border sm:grid-cols-3 lg:grid-cols-5"
		>
			{CELL_SPANS.map((span, i) => (
				<div
					key={i}
					className={cn(
						'border-border -mt-px -ml-px min-w-0 border-t border-l px-3.5 py-3 sm:px-4',
						span,
					)}
				>
					<div className="flex h-4 items-center">
						<Skeleton className="h-2.5 w-16" />
					</div>
					<div className="mt-1 flex h-8 items-center">
						<Skeleton className="h-5 w-20" />
					</div>
					<div className="mt-0.5 flex h-4 items-center">
						<Skeleton className="h-2 w-24" />
					</div>
				</div>
			))}
		</section>
	);
}

function Cell({
	label,
	hint,
	href,
	className,
	children,
}: {
	label: string;
	hint?: ReactNode;
	href?: string;
	className?: string;
	children: ReactNode;
}) {
	const body = (
		<>
			<p className="text-muted-foreground flex items-center gap-1 text-xs font-medium">
				{label}
				{href && (
					<ArrowUpRight
						className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100"
						aria-hidden="true"
					/>
				)}
			</p>
			<div className="mt-1">{children}</div>
			{hint && <p className="text-muted-foreground mt-0.5 truncate text-xs">{hint}</p>}
		</>
	);
	// Divided cells: every cell draws its own top/left hairline, so the grid
	// reads as one card at any column count without doubled borders.
	const cellClass = cn(
		'border-border -mt-px -ml-px min-w-0 border-t border-l px-3.5 py-3 sm:px-4',
		className,
	);
	return (
		<motion.div variants={cellVariant} className={cellClass}>
			{href ? (
				<AppLink
					href={href}
					className="group hover:bg-muted/40 focus-visible:ring-ring -mx-3.5 -my-3 block px-3.5 py-3 transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset sm:-mx-4 sm:px-4"
				>
					{body}
				</AppLink>
			) : (
				body
			)}
		</motion.div>
	);
}

/**
 * A number that counts from its previous value to the new one. The final
 * value is always what screen readers get (the moving digits are hidden from
 * them), and reduced motion jumps straight there.
 */
function CountUp({
	value,
	decimals = 0,
	suffix = '',
	className,
}: {
	value: number;
	decimals?: number;
	suffix?: string;
	className?: string;
}) {
	const ref = useRef<HTMLSpanElement>(null);
	const from = useRef(0);
	const reduce = useReducedMotion();
	const format = (n: number) =>
		`${n.toLocaleString(undefined, {
			minimumFractionDigits: decimals,
			maximumFractionDigits: decimals,
		})}${suffix}`;
	const final = format(value);

	useEffect(() => {
		const node = ref.current;
		if (!node) return;
		if (reduce) {
			node.textContent = final;
			from.current = value;
			return;
		}
		const controls = animate(from.current, value, {
			duration: 0.7,
			ease: [0.2, 0.8, 0.2, 1],
			onUpdate: (n) => {
				node.textContent = format(n);
			},
			onComplete: () => {
				node.textContent = final;
			},
		});
		from.current = value;
		return () => controls.stop();
		// `format` is derived from decimals/suffix, already covered by `final`.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [value, final, reduce]);

	return (
		<span className={cn('tabular-nums', className)}>
			<span className="sr-only">{final}</span>
			<span ref={ref} aria-hidden="true">
				{final}
			</span>
		</span>
	);
}

function TierMeter({ tier, label }: { tier: HealthTier | LatencyTier; label: string }) {
	const level = TIER_LEVEL[tier];
	return (
		<span className="mb-1.5 flex items-center gap-1.5" title={label}>
			<span className="flex items-end gap-0.5" aria-hidden="true">
				{[1, 2, 3].map((bar) => (
					<span
						key={bar}
						className={cn(
							'w-1 rounded-sm transition-colors',
							bar === 1 ? 'h-1.5' : bar === 2 ? 'h-2.5' : 'h-3.5',
							bar <= level ? TIER_BAR[tier] : 'bg-muted',
						)}
					/>
				))}
			</span>
			<span className="text-muted-foreground hidden text-[11px] font-medium sm:inline">
				{label}
			</span>
		</span>
	);
}

function ApiTiles({ apis, extra }: { apis: EntityUsageRow[]; extra: number }) {
	if (apis.length === 0) return null;
	return (
		<span className="mb-1 flex -space-x-1.5" aria-hidden="true">
			{apis.map((api, i) => {
				const color = API_PALETTE[i % API_PALETTE.length]!;
				return (
					<motion.span
						key={api.id}
						title={api.label}
						initial={{ scale: 0.6, opacity: 0 }}
						animate={{ scale: 1, opacity: 1 }}
						transition={{
							delay: 0.15 + i * 0.05,
							type: 'spring',
							stiffness: 420,
							damping: 22,
						}}
						className="ring-card flex h-6 w-6 items-center justify-center rounded-md text-[9px] font-bold ring-2"
						style={{ backgroundColor: color, color: textColor(color) }}
					>
						{getInitials(api.label)}
					</motion.span>
				);
			})}
			{extra > 0 && (
				<span className="bg-muted text-muted-foreground ring-card flex h-6 min-w-6 items-center justify-center rounded-md px-1 text-[9px] font-bold ring-2">
					+{extra}
				</span>
			)}
		</span>
	);
}

/** A soft area sparkline that draws itself in. Decorative — the number beside it is the data. */
function AreaSpark({ data, className }: { data: number[]; className?: string }) {
	const gradientId = useId();
	const reduce = useReducedMotion();
	if (data.length < 2) return null;
	const w = 96;
	const h = 28;
	const max = Math.max(...data, 1);
	const points = data.map((v, i) => [(i / (data.length - 1)) * w, h - 2 - (v / max) * (h - 4)]);
	const line = `M ${points.map(([x, y]) => `${x.toFixed(1)},${y!.toFixed(1)}`).join(' L ')}`;
	const area = `${line} L ${w},${h} L 0,${h} Z`;
	return (
		<svg
			viewBox={`0 0 ${w} ${h}`}
			className={className}
			preserveAspectRatio="none"
			aria-hidden="true"
		>
			<defs>
				<linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
					<stop offset="100%" stopColor="currentColor" stopOpacity="0" />
				</linearGradient>
			</defs>
			<motion.path
				d={area}
				fill={`url(#${gradientId})`}
				initial={reduce ? false : { opacity: 0 }}
				animate={{ opacity: 1 }}
				transition={{ delay: 0.35, duration: 0.4 }}
			/>
			<motion.path
				d={line}
				fill="none"
				stroke="currentColor"
				strokeWidth={1.5}
				strokeLinecap="round"
				strokeLinejoin="round"
				vectorEffect="non-scaling-stroke"
				initial={reduce ? false : { pathLength: 0 }}
				animate={{ pathLength: 1 }}
				transition={{ duration: 0.8, ease: [0.2, 0.8, 0.2, 1] }}
			/>
		</svg>
	);
}
