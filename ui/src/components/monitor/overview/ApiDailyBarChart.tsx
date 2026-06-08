import { type JSX, useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { TimelinePoint, TimeRange } from '@/components/monitor/types';
import { getVendorConfig, getInitials } from '@/components/monitor/shared/vendor-icons';
import { cn } from '@/lib/utils';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';

type GroupMode = 'apis' | 'toolkits' | 'agents';

interface ApiDailyBarChartProps {
	points: TimelinePoint[];
	timeRange: TimeRange;
	className?: string;
}

const SHORT_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

const TOOLKIT_COLORS: Record<string, { bg: string; text: string }> = {
	'My Integrations': { bg: '#6366f1', text: '#fff' },
	'CI/CD Toolkit': { bg: '#f59e0b', text: '#fff' },
	'Support Bot': { bg: '#10b981', text: '#fff' },
	'Data Sync Toolkit': { bg: '#3b82f6', text: '#fff' },
	'Marketing Automation': { bg: '#ec4899', text: '#fff' },
};

const FALLBACK_COLORS = [
	{ bg: '#8b5cf6', text: '#fff' },
	{ bg: '#14b8a6', text: '#fff' },
	{ bg: '#f97316', text: '#fff' },
	{ bg: '#06b6d4', text: '#fff' },
	{ bg: '#84cc16', text: '#fff' },
];

const AGENT_COLORS: Array<{ bg: string; text: string }> = [
	{ bg: '#0891b2', text: '#fff' },
	{ bg: '#7c3aed', text: '#fff' },
	{ bg: '#db2777', text: '#fff' },
	{ bg: '#16a34a', text: '#fff' },
	{ bg: '#ea580c', text: '#fff' },
	{ bg: '#475569', text: '#fff' },
];

function getToolkitColor(name: string, index: number) {
	return TOOLKIT_COLORS[name] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length];
}

function getAgentColor(index: number) {
	return AGENT_COLORS[index % AGENT_COLORS.length];
}

interface BarBucket {
	dayLabel: string;
	dateLabel: string;
	total: number;
	segments: Array<{
		key: string;
		label: string;
		count: number;
		color: string;
		iconUrl?: string;
		textColor: string;
	}>;
}

interface BucketDef {
	label: string;
	subLabel: string;
	start: number;
	end: number;
}

function getBucketDefs(timeRange: TimeRange): BucketDef[] {
	const now = Date.now();
	const MS_MIN = 60_000;
	const MS_HR = 3_600_000;
	const MS_DAY = 86_400_000;

	switch (timeRange) {
		case '1h': {
			const defs: BucketDef[] = [];
			for (let i = 5; i >= 0; i--) {
				const end = now - i * 10 * MS_MIN;
				const start = end - 10 * MS_MIN;
				const d = new Date(end);
				const h = d.getHours();
				const m = d.getMinutes();
				defs.push({
					label: `${h}:${String(m).padStart(2, '0')}`,
					subLabel: '',
					start,
					end,
				});
			}
			return defs;
		}
		case '24h': {
			const defs: BucketDef[] = [];
			for (let i = 5; i >= 0; i--) {
				const end = now - i * 4 * MS_HR;
				const start = end - 4 * MS_HR;
				const ds = new Date(start);
				const de = new Date(end);
				defs.push({
					label: `${ds.getHours()}:00`,
					subLabel: `–${de.getHours()}:00`,
					start,
					end,
				});
			}
			return defs;
		}
		case '7d': {
			const defs: BucketDef[] = [];
			for (let i = 6; i >= 0; i--) {
				const d = new Date(now - i * MS_DAY);
				const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
				defs.push({
					label: SHORT_DAYS[d.getDay()],
					subLabel: `${d.getMonth() + 1}/${d.getDate()}`,
					start: dayStart,
					end: dayStart + MS_DAY,
				});
			}
			return defs;
		}
		case '30d':
		case 'all': {
			const defs: BucketDef[] = [];
			const bucketCount = 6;
			const span = (timeRange === 'all' ? 60 : 30) * MS_DAY;
			const bucketSize = span / bucketCount;
			for (let i = 0; i < bucketCount; i++) {
				const start = now - span + i * bucketSize;
				const end = start + bucketSize;
				const ds = new Date(start);
				const de = new Date(end);
				defs.push({
					label: `${ds.getMonth() + 1}/${ds.getDate()}`,
					subLabel: `–${de.getMonth() + 1}/${de.getDate()}`,
					start,
					end,
				});
			}
			return defs;
		}
	}
}

const SUBTITLE_MAP: Record<TimeRange, string> = {
	'1h': 'Last hour (10-min intervals)',
	'24h': 'Last 24 hours (4-hour intervals)',
	'7d': 'Last 7 days',
	'30d': 'Last 30 days (5-day intervals)',
	all: 'All time (10-day intervals)',
};

function buildBars(points: TimelinePoint[], mode: GroupMode, timeRange: TimeRange): BarBucket[] {
	const defs = getBucketDefs(timeRange);
	const buckets: Map<string, TimelinePoint[]>[] = defs.map(() => new Map());

	for (const pt of points) {
		for (let i = 0; i < defs.length; i++) {
			if (pt.timestamp >= defs[i].start && pt.timestamp < defs[i].end) {
				const key =
					mode === 'apis' ? pt.vendor : mode === 'toolkits' ? pt.toolkitName : pt.agentId;
				const bucket = buckets[i];
				const existing = bucket.get(key);
				if (existing) existing.push(pt);
				else bucket.set(key, [pt]);
				break;
			}
		}
	}

	let toolkitIndex = 0;
	const toolkitIndexMap = new Map<string, number>();
	let agentIndex = 0;
	const agentIndexMap = new Map<string, number>();

	return defs.map((def, i) => {
		const bucket = buckets[i];
		let total = 0;
		const segments: BarBucket['segments'] = [];
		const sortedKeys = Array.from(bucket.entries()).sort((a, b) => b[1].length - a[1].length);

		for (const [key, pts] of sortedKeys) {
			total += pts.length;

			if (mode === 'apis') {
				const config = getVendorConfig(key);
				segments.push({
					key,
					label: pts[0].apiName,
					count: pts.length,
					color: config.bg,
					iconUrl: config.iconUrl,
					textColor: config.text,
				});
			} else if (mode === 'toolkits') {
				if (!toolkitIndexMap.has(key)) {
					toolkitIndexMap.set(key, toolkitIndex++);
				}
				const ac = getToolkitColor(key, toolkitIndexMap.get(key)!);
				segments.push({
					key,
					label: key,
					count: pts.length,
					color: ac.bg,
					textColor: ac.text,
				});
			} else {
				if (!agentIndexMap.has(key)) {
					agentIndexMap.set(key, agentIndex++);
				}
				const ac = getAgentColor(agentIndexMap.get(key)!);
				segments.push({
					key,
					label: pts[0].agentName,
					count: pts.length,
					color: ac.bg,
					textColor: ac.text,
				});
			}
		}

		return {
			dayLabel: def.label,
			dateLabel: def.subLabel,
			total,
			segments,
		};
	});
}

interface TooltipState {
	bar: BarBucket;
	x: number;
	y: number;
}

export function ApiDailyBarChart({
	points,
	timeRange,
	className,
}: ApiDailyBarChartProps): JSX.Element {
	const containerRef = useRef<HTMLDivElement>(null);
	const [width, setWidth] = useState(700);
	const [mode, setMode] = useState<GroupMode>('apis');
	const [tooltip, setTooltip] = useState<TooltipState | null>(null);
	const [hoveredSegKey, setHoveredSegKey] = useState<string | null>(null);

	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) setWidth(Math.max(300, entry.contentRect.width));
		});
		observer.observe(el);
		return () => observer.disconnect();
	}, []);

	const bars = useMemo(() => buildBars(points, mode, timeRange), [points, mode, timeRange]);
	const barCount = bars.length;
	const rawMax = useMemo(() => Math.max(...bars.map((b) => b.total), 1), [bars]);
	const maxTotal = Math.ceil(rawMax * 1.15);

	const PADDING = { top: 16, bottom: 40, left: 40, right: 16 };
	const BAR_HEIGHT = 320;
	const svgH = PADDING.top + BAR_HEIGHT + PADDING.bottom;
	const chartW = width - PADDING.left - PADDING.right;
	const barW = Math.max(20, chartW / barCount - 8);
	const gap = barCount > 1 ? (chartW - barW * barCount) / (barCount - 1) : 0;

	const yTicks = useMemo(() => {
		const step = Math.ceil(maxTotal / 4);
		return Array.from({ length: 5 }, (_, i) => i * step);
	}, [maxTotal]);

	const toY = useCallback(
		(count: number) => PADDING.top + BAR_HEIGHT - (count / maxTotal) * BAR_HEIGHT,
		[maxTotal],
	);

	const allSegments = useMemo(() => {
		const map = new Map<
			string,
			{
				key: string;
				label: string;
				color: string;
				iconUrl?: string;
				textColor: string;
				total: number;
			}
		>();
		for (const bar of bars) {
			for (const seg of bar.segments) {
				const existing = map.get(seg.key);
				if (existing) existing.total += seg.count;
				else map.set(seg.key, { ...seg, total: seg.count });
			}
		}
		return Array.from(map.values()).sort((a, b) => b.total - a.total);
	}, [bars]);

	if (points.length === 0) {
		return (
			<div
				ref={containerRef}
				className={cn(
					'border-border bg-card flex items-center justify-center rounded-xl border p-16',
					className,
				)}
			>
				<p className="text-muted-foreground text-sm">No execution data available</p>
			</div>
		);
	}

	return (
		<div
			ref={containerRef}
			className={cn(
				'border-border bg-card relative min-w-0 overflow-hidden rounded-xl border',
				className,
			)}
		>
			<div className="flex items-start justify-between px-4 pt-3 pb-0">
				<div>
					<h3 className="text-foreground text-sm font-medium">Execution Volume</h3>
					<p className="text-muted-foreground text-xs">
						{SUBTITLE_MAP[timeRange]}, colored by{' '}
						{mode === 'apis' ? 'API' : mode === 'toolkits' ? 'toolkit' : 'agent'}
					</p>
				</div>

				<SegmentedToggle
					layoutId="dailyBarToggle"
					options={[
						{ value: 'apis', label: 'APIs' },
						{ value: 'toolkits', label: 'Toolkits' },
						{ value: 'agents', label: 'Agents' },
					]}
					value={mode}
					onChange={setMode}
				/>
			</div>

			<svg width={width} height={svgH} viewBox={`0 0 ${width} ${svgH}`}>
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
						key={mode}
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
									key={i}
									onMouseEnter={() => {
										setTooltip({ bar, x: bx + barW / 2, y: PADDING.top });
									}}
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
										const isDimmed = hoveredSegKey && hoveredSegKey !== seg.key;

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
												onMouseEnter={() => setHoveredSegKey(seg.key)}
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
										style={{ fontFamily: 'var(--font-sans, system-ui)' }}
									>
										{bar.dayLabel}
									</text>
									<text
										x={bx + barW / 2}
										y={PADDING.top + BAR_HEIGHT + 30}
										textAnchor="middle"
										fontSize={9}
										fill="currentColor"
										opacity={0.35}
										style={{ fontFamily: 'var(--font-sans, system-ui)' }}
									>
										{bar.dateLabel}
									</text>
								</g>
							);
						})}
					</motion.g>
				</AnimatePresence>
			</svg>

			<AnimatePresence mode="wait">
				<motion.div
					key={mode}
					className="border-border flex flex-wrap items-center gap-3 border-t px-4 py-2.5"
					initial={{ opacity: 0 }}
					animate={{ opacity: 1 }}
					exit={{ opacity: 0 }}
					transition={{ duration: 0.2 }}
				>
					{allSegments.map((seg) => (
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
							<div
								className="flex h-4 w-4 items-center justify-center overflow-hidden rounded-full"
								style={{ backgroundColor: seg.color }}
							>
								{seg.iconUrl ? (
									<img
										src={seg.iconUrl}
										alt={seg.label}
										className="h-2.5 w-2.5 object-contain"
										style={{
											filter:
												seg.textColor === '#fff' ? 'invert(1)' : undefined,
										}}
									/>
								) : (
									<span
										className="text-[6px] font-bold"
										style={{ color: seg.textColor }}
									>
										{getInitials(seg.label)}
									</span>
								)}
							</div>
							<span className="text-foreground text-[11px]">{seg.label}</span>
						</button>
					))}
				</motion.div>
			</AnimatePresence>

			{tooltip && (
				<BarTooltip bar={tooltip.bar} x={tooltip.x} y={tooltip.y} containerWidth={width} />
			)}
		</div>
	);
}

function BarTooltip({
	bar,
	x,
	y,
	containerWidth,
}: {
	bar: BarBucket;
	x: number;
	y: number;
	containerWidth: number;
}): JSX.Element {
	const isRight = x > containerWidth * 0.6;

	return (
		<div
			className="border-border bg-card pointer-events-none absolute z-30 w-52 rounded-lg border p-3 shadow-xl"
			style={{
				left: isRight ? x - 220 : x + 20,
				top: Math.max(8, y),
			}}
		>
			<div className="mb-2 flex items-center justify-between">
				<span className="text-foreground text-xs font-medium">
					{bar.dayLabel} {bar.dateLabel}
				</span>
				<span className="text-muted-foreground text-xs">{bar.total} total</span>
			</div>

			<div className="space-y-1.5">
				{bar.segments.map((seg) => (
					<div key={seg.key} className="flex items-center gap-2">
						<div
							className="flex h-4 w-4 flex-shrink-0 items-center justify-center overflow-hidden rounded-full"
							style={{ backgroundColor: seg.color }}
						>
							{seg.iconUrl ? (
								<img
									src={seg.iconUrl}
									alt={seg.label}
									className="h-2.5 w-2.5 object-contain"
									style={{
										filter: seg.textColor === '#fff' ? 'invert(1)' : undefined,
									}}
								/>
							) : (
								<span
									className="text-[6px] font-bold"
									style={{ color: seg.textColor }}
								>
									{getInitials(seg.label)}
								</span>
							)}
						</div>
						<span className="text-foreground flex-1 truncate text-xs">{seg.label}</span>
						<span className="text-foreground text-xs font-medium">{seg.count}</span>
					</div>
				))}
			</div>
		</div>
	);
}
