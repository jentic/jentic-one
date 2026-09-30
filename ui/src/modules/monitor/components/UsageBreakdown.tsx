/**
 * UsageBreakdown.
 *
 * The Usage tab's one table: the top entities for the active lens (APIs /
 * Agents) with Trend (sparkline), Success (health dot), Calls
 * (relative bar + count) and Latency (avg) columns, fed by the enriched
 * `GET /monitoring/usage` aggregation for that lens only.
 *
 * Controlled: the Overview owns the lens and hands down `rowHref`, which turns
 * a row into a drill-down into the API calls log. Rows without a filterable
 * identity (the Unattributed bucket) stay static.
 *
 * Columns sort client-side over the returned top rows; rows collapse into a
 * stacked layout on narrow viewports.
 */
import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronRight } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import {
	HEALTH_DOT_CLASS,
	HEALTH_LABEL,
	LATENCY_TEXT_CLASS,
	healthTier,
	latencyTier,
} from '@/shared/lib/usageThresholds';
import { AppLink, LoadingState, SegmentedToggle, SparklineChart } from '@/shared/ui';
import { formatLatency, formatPercent } from '@/modules/monitor/lib/format';
import { getInitials, lensPalette, type UsageLens } from '@/modules/monitor/lib/palette';
import type { EntityUsageRow } from '@/modules/monitor/lib/usage';

export type BreakdownSortKey = 'calls' | 'success' | 'latency';

interface UsageBreakdownProps {
	lens: UsageLens;
	onLensChange: (lens: UsageLens) => void;
	rows: EntityUsageRow[];
	isLoading?: boolean;
	/** Drill-down target for a row, or null when the row can't be filtered on. */
	rowHref?: (row: EntityUsageRow) => string | null;
}

const LENS_OPTIONS: { value: UsageLens; label: string }[] = [
	{ value: 'apis', label: 'APIs' },
	{ value: 'agents', label: 'Agents' },
];

const LENS_SUBTITLES: Record<UsageLens, string> = {
	apis: 'Top APIs by traffic — select one to see its executions',
	agents: 'Top agents by traffic — select one to see its executions',
};

const LENS_NOUNS: Record<UsageLens, string> = {
	apis: 'API',
	agents: 'agent',
};

const GRID = 'sm:grid sm:grid-cols-[1fr_72px_88px_120px_72px_16px] sm:items-center sm:gap-3';

function sortRows(
	rows: EntityUsageRow[],
	key: BreakdownSortKey,
	dir: 'asc' | 'desc',
): EntityUsageRow[] {
	const value = (r: EntityUsageRow) =>
		key === 'calls' ? r.totalExecutions : key === 'success' ? r.successRate : r.avgLatencyMs;
	const sign = dir === 'asc' ? 1 : -1;
	return [...rows].sort((a, b) => sign * (value(a) - value(b)));
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

function SortHeader({
	label,
	sortKey,
	active,
	dir,
	onSort,
	align = 'left',
}: {
	label: string;
	sortKey: BreakdownSortKey;
	active: boolean;
	dir: 'asc' | 'desc';
	onSort: (key: BreakdownSortKey) => void;
	align?: 'left' | 'center' | 'right';
}) {
	const Icon = dir === 'asc' ? ArrowUp : ArrowDown;
	return (
		<span
			role="columnheader"
			aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
			className={cn(
				'flex',
				align === 'center' && 'justify-center',
				align === 'right' && 'justify-end',
			)}
		>
			<button
				type="button"
				onClick={() => onSort(sortKey)}
				className={cn(
					'hover:text-foreground inline-flex items-center gap-1 tracking-wider uppercase',
					active && 'text-foreground',
				)}
				aria-label={`Sort by ${label.toLowerCase()}`}
			>
				{label}
				{active && <Icon className="h-3 w-3" aria-hidden="true" />}
			</button>
		</span>
	);
}

export function UsageBreakdown({
	lens,
	onLensChange,
	rows,
	isLoading = false,
	rowHref,
}: UsageBreakdownProps) {
	const [sortKey, setSortKey] = useState<BreakdownSortKey>('calls');
	const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

	const onSort = (key: BreakdownSortKey) => {
		if (key === sortKey) {
			setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
			return;
		}
		setSortKey(key);
		// Worst-first is the useful default: lowest success, slowest latency.
		setSortDir(key === 'success' ? 'asc' : 'desc');
	};

	const palette = lensPalette(lens);
	// Colours follow traffic rank, not the current sort, so a row keeps its
	// colour when the table is re-sorted.
	const colorById = useMemo(
		() => new Map(rows.map((r, i) => [r.id, palette[i % palette.length]])),
		[rows, palette],
	);
	const sorted = useMemo(() => sortRows(rows, sortKey, sortDir), [rows, sortKey, sortDir]);
	const maxExec = useMemo(() => Math.max(1, ...rows.map((r) => r.totalExecutions)), [rows]);

	return (
		<section
			className="border-border bg-card rounded-xl border"
			aria-labelledby="breakdown-heading"
		>
			<div className="border-border flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
				<div>
					<h2 id="breakdown-heading" className="text-foreground text-sm font-semibold">
						Breakdown
					</h2>
					<p className="text-muted-foreground text-xs">{LENS_SUBTITLES[lens]}</p>
				</div>
				<SegmentedToggle
					options={LENS_OPTIONS}
					value={lens}
					onChange={onLensChange}
					ariaLabel="Breakdown grouping"
				/>
			</div>

			<div role="table" aria-label={`Usage by ${LENS_NOUNS[lens]}`}>
				<div role="rowgroup">
					<div
						role="row"
						className={cn(
							'border-border/50 text-muted-foreground hidden border-b px-4 py-2 text-[10px] font-medium',
							GRID,
						)}
					>
						<span role="columnheader" className="tracking-wider uppercase">
							Name
						</span>
						<span role="columnheader" className="text-center tracking-wider uppercase">
							Trend
						</span>
						<SortHeader
							label="Success"
							sortKey="success"
							active={sortKey === 'success'}
							dir={sortDir}
							onSort={onSort}
							align="center"
						/>
						<SortHeader
							label="Calls"
							sortKey="calls"
							active={sortKey === 'calls'}
							dir={sortDir}
							onSort={onSort}
						/>
						<SortHeader
							label="Latency"
							sortKey="latency"
							active={sortKey === 'latency'}
							dir={sortDir}
							onSort={onSort}
							align="right"
						/>
						<span aria-hidden="true" />
					</div>
				</div>

				<div role="rowgroup">
					{isLoading && rows.length === 0 ? (
						<div className="py-8">
							<LoadingState />
						</div>
					) : sorted.length === 0 ? (
						<div className="flex items-center justify-center py-12">
							<p className="text-muted-foreground text-sm">
								No {LENS_NOUNS[lens]} data in this window
							</p>
						</div>
					) : (
						sorted.map((row, i) => (
							<BreakdownRow
								key={row.id}
								row={row}
								color={colorById.get(row.id) ?? palette[0]}
								maxExec={maxExec}
								isLast={i === sorted.length - 1}
								href={rowHref?.(row) ?? null}
							/>
						))
					)}
				</div>
			</div>
		</section>
	);
}

function BreakdownRow({
	row,
	color,
	maxExec,
	isLast,
	href,
}: {
	row: EntityUsageRow;
	color: string;
	maxExec: number;
	isLast: boolean;
	href: string | null;
}) {
	const health = healthTier(row.successRate);
	const ratio = row.totalExecutions / maxExec;
	const latencyClass = cn(
		'text-xs tabular-nums font-medium',
		LATENCY_TEXT_CLASS[latencyTier(row.avgLatencyMs)],
	);
	const healthTitle = `${formatPercent(row.successRate)} success — ${HEALTH_LABEL[health]}`;

	const content = (
		<>
			<div role="cell" className="flex min-w-0 items-center gap-2.5">
				<div
					className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[10px] font-bold text-white"
					style={{ backgroundColor: color }}
					aria-hidden="true"
				>
					{getInitials(row.label)}
				</div>
				<span className="text-foreground min-w-0 flex-1 truncate text-sm font-medium">
					{row.label}
				</span>
				<span className={cn('shrink-0 sm:hidden', latencyClass)}>
					{formatLatency(row.avgLatencyMs)}
				</span>
			</div>

			{/* Narrow-viewport stacked layout */}
			<div className="mt-2 flex items-center gap-3 sm:hidden" aria-hidden="true">
				<SparklineChart
					data={row.trend}
					width={56}
					height={18}
					strokeWidth={1.5}
					color={color}
				/>
				<div className="flex items-center gap-1.5" title={healthTitle}>
					<div className={cn('h-2 w-2 rounded-full', HEALTH_DOT_CLASS[health])} />
					<span className="text-muted-foreground text-[11px] tabular-nums">
						{formatPercent(row.successRate)}
					</span>
				</div>
				<span className="text-muted-foreground ml-auto text-[11px] tabular-nums">
					{row.totalExecutions.toLocaleString()} calls
				</span>
			</div>

			{/* Desktop grid columns */}
			<div role="cell" className="hidden items-center justify-center sm:flex">
				<SparklineChart
					data={row.trend}
					width={56}
					height={20}
					strokeWidth={1.5}
					color={color}
				/>
			</div>
			<div
				role="cell"
				className="hidden items-center justify-center gap-1.5 sm:flex"
				title={healthTitle}
			>
				<div className={cn('h-2 w-2 rounded-full', HEALTH_DOT_CLASS[health])} />
				<span className="text-muted-foreground text-[11px] tabular-nums">
					{formatPercent(row.successRate)}
				</span>
			</div>
			<div role="cell" className="hidden flex-col gap-0.5 sm:flex">
				<VolumeBar ratio={ratio} color={color} />
				<span className="text-muted-foreground text-[10px] tabular-nums">
					{row.totalExecutions.toLocaleString()} calls
				</span>
			</div>
			<div role="cell" className="hidden text-right sm:block">
				<span className={latencyClass}>{formatLatency(row.avgLatencyMs)}</span>
			</div>
			<div role="cell" className="hidden sm:block">
				{href && (
					<ChevronRight
						className="text-muted-foreground group-hover:text-foreground h-4 w-4"
						aria-hidden="true"
					/>
				)}
			</div>
		</>
	);

	const className = cn(
		'group block px-4 py-2.5 transition-colors',
		GRID,
		href && 'hover:bg-muted/30',
		!isLast && 'border-border/30 border-b',
	);

	return href ? (
		<AppLink
			href={href}
			role="row"
			className={className}
			aria-label={`View executions for ${row.label}`}
		>
			{content}
		</AppLink>
	) : (
		<div role="row" className={className}>
			{content}
		</div>
	);
}
