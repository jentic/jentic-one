/**
 * UsageBreakdown — ported from jentic-mini's `BreakdownSection`.
 *
 * The "Breakdown" table with a segmented APIs / Toolkits / Agents toggle and
 * per-row Trend (sparkline), Health (success-rate dot), Volume (relative bar
 * + call count), and Speed (avg latency) columns. Replaces the interim
 * `TopOperations` panel that could only show busiest operations from the old
 * `GET /monitoring/executions` endpoint.
 *
 * Rows collapse into a stacked layout on narrow viewports, same as mini.
 */
import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';
import { SegmentedToggle, SparklineChart } from '@/shared/ui';
import { formatLatency, formatPercent } from '@/modules/monitor/lib/format';
import { getInitials, lensPalette, type UsageLens } from '@/modules/monitor/lib/palette';
import type { EntityUsageRow } from '@/modules/monitor/lib/usage';

interface UsageBreakdownProps {
	apis: EntityUsageRow[];
	toolkits: EntityUsageRow[];
	agents: EntityUsageRow[];
}

const LENS_SUBTITLES: Record<UsageLens, string> = {
	apis: 'Performance for each connected API',
	toolkits: 'Activity for each toolkit',
	agents: 'Activity per agent identity',
};

const LENS_NOUNS: Record<UsageLens, string> = {
	apis: 'API',
	toolkits: 'toolkit',
	agents: 'agent',
};

function getHealthDot(successRate: number): { color: string; label: string } {
	if (successRate >= 97) return { color: 'bg-accent-green', label: 'Healthy' };
	if (successRate >= 90) return { color: 'bg-accent-amber', label: 'Degraded' };
	return { color: 'bg-accent-pink', label: 'Issues' };
}

function VolumeBar({ ratio, color }: { ratio: number; color: string }) {
	return (
		<div className="bg-muted/50 h-1.5 w-full rounded-full">
			<div
				className="h-full rounded-full transition-all duration-500"
				style={{ width: `${Math.max(4, ratio * 100)}%`, backgroundColor: color }}
			/>
		</div>
	);
}

export function UsageBreakdown({ apis, toolkits, agents }: UsageBreakdownProps) {
	const [lens, setLens] = useState<UsageLens>('apis');

	const items = lens === 'apis' ? apis : lens === 'toolkits' ? toolkits : agents;
	const palette = lensPalette(lens);

	const maxExec = useMemo(() => Math.max(1, ...items.map((r) => r.totalExecutions)), [items]);

	return (
		<div className="border-border bg-card rounded-xl border">
			<div className="border-border flex items-center justify-between gap-2 border-b px-4 py-3">
				<div>
					<h2 className="text-foreground text-sm font-semibold">Breakdown</h2>
					<p className="text-muted-foreground text-xs">{LENS_SUBTITLES[lens]}</p>
				</div>
				<SegmentedToggle
					options={[
						{ value: 'apis', label: 'APIs' },
						{ value: 'toolkits', label: 'Toolkits' },
						{ value: 'agents', label: 'Agents' },
					]}
					value={lens}
					onChange={setLens}
					ariaLabel="Breakdown grouping"
				/>
			</div>

			<div className="border-border/50 text-muted-foreground hidden items-center gap-3 border-b px-4 py-2 text-[10px] font-medium tracking-wider uppercase sm:grid sm:grid-cols-[1fr_72px_56px_100px_56px]">
				<span>Name</span>
				<span className="text-center">Trend</span>
				<span className="text-center">Health</span>
				<span>Volume</span>
				<span className="text-right">Speed</span>
			</div>

			<AnimatePresence mode="wait">
				{items.length === 0 ? (
					<motion.div
						key={`empty-${lens}`}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						className="flex items-center justify-center py-12"
					>
						<p className="text-muted-foreground text-sm">
							No {LENS_NOUNS[lens]} data available
						</p>
					</motion.div>
				) : (
					<motion.div
						key={lens}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: 0.2 }}
					>
						{items.map((row, i) => (
							<BreakdownRow
								key={row.id}
								row={row}
								color={palette[i % palette.length]}
								maxExec={maxExec}
								index={i}
								isLast={i === items.length - 1}
							/>
						))}
					</motion.div>
				)}
			</AnimatePresence>
		</div>
	);
}

function BreakdownRow({
	row,
	color,
	maxExec,
	index,
	isLast,
}: {
	row: EntityUsageRow;
	color: string;
	maxExec: number;
	index: number;
	isLast: boolean;
}) {
	const health = getHealthDot(row.successRate);
	const ratio = row.totalExecutions / maxExec;

	const latencyClass = cn(
		'text-xs tabular-nums font-medium',
		row.avgLatencyMs <= 300
			? 'text-accent-green'
			: row.avgLatencyMs <= 800
				? 'text-foreground'
				: 'text-accent-pink',
	);

	return (
		<motion.div
			initial={{ opacity: 0, y: 8 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{ delay: index * 0.04, duration: 0.25 }}
			className={cn(
				'group hover:bg-muted/30 px-4 py-2.5 transition-colors',
				'sm:grid sm:grid-cols-[1fr_72px_56px_100px_56px] sm:items-center sm:gap-3',
				!isLast && 'border-border/30 border-b',
			)}
		>
			<div className="flex min-w-0 items-center gap-2.5">
				<div
					className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[10px] font-bold text-white"
					style={{ backgroundColor: color }}
				>
					{getInitials(row.label)}
				</div>
				<div className="min-w-0 flex-1">
					<span className="text-foreground truncate text-sm font-medium">
						{row.label}
					</span>
				</div>
				<span className={cn('shrink-0 sm:hidden', latencyClass)}>
					{formatLatency(row.avgLatencyMs)}
				</span>
			</div>

			{/* Narrow-viewport stacked layout */}
			<div className="mt-2 flex items-center gap-3 sm:hidden">
				<SparklineChart
					data={row.trend}
					width={56}
					height={18}
					strokeWidth={1.5}
					color={color}
				/>
				<div
					className="flex items-center gap-1.5"
					title={`${formatPercent(row.successRate)} success — ${health.label}`}
				>
					<div className={cn('h-2 w-2 rounded-full', health.color)} />
					<span className="text-muted-foreground text-[11px] tabular-nums">
						{formatPercent(row.successRate)}
					</span>
				</div>
				<span className="text-muted-foreground ml-auto text-[11px] tabular-nums">
					{row.totalExecutions.toLocaleString()} calls
				</span>
			</div>
			<div className="mt-1.5 sm:hidden">
				<VolumeBar ratio={ratio} color={color} />
			</div>

			{/* Desktop grid columns */}
			<div className="hidden items-center justify-center sm:flex">
				<SparklineChart
					data={row.trend}
					width={56}
					height={20}
					strokeWidth={1.5}
					color={color}
				/>
			</div>

			<div
				className="hidden items-center justify-center gap-1.5 sm:flex"
				title={`${formatPercent(row.successRate)} success — ${health.label}`}
			>
				<div className={cn('h-2 w-2 rounded-full', health.color)} />
				<span className="text-muted-foreground text-[11px] tabular-nums">
					{formatPercent(row.successRate)}
				</span>
			</div>

			<div className="hidden flex-col gap-0.5 sm:flex">
				<VolumeBar ratio={ratio} color={color} />
				<span className="text-muted-foreground text-[10px] tabular-nums">
					{row.totalExecutions.toLocaleString()} calls
				</span>
			</div>

			<div className="hidden text-right sm:block">
				<span className={latencyClass}>{formatLatency(row.avgLatencyMs)}</span>
			</div>
		</motion.div>
	);
}
