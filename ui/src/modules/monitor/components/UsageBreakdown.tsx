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
import { useMemo, useState, type CSSProperties } from 'react';
import { ArrowDown, ArrowUp, ChevronRight } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import {
	HEALTH_DOT_CLASS,
	HEALTH_LABEL,
	LATENCY_TEXT_CLASS,
	healthTier,
	latencyTier,
} from '@/shared/lib/usageThresholds';
import {
	AgentBadge,
	AppLink,
	LoadingState,
	SegmentedToggle,
	SparklineChart,
	VendorIcon,
	avatarToneIndex,
} from '@/shared/ui';
import { formatLatency, formatPercent } from '@/modules/monitor/lib/format';
import {
	assignChartTones,
	toneFor,
	type EntityTone,
	type UsageLens,
} from '@/modules/monitor/lib/palette';
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

	// Tones are assigned over the traffic-ranked rows (not the current sort),
	// exactly as the volume and bubble charts assign them, so a row keeps its
	// colour when re-sorted and matches its series in the charts above.
	const tones = useMemo(() => assignChartTones(lens, rows), [lens, rows]);
	const sorted = useMemo(() => sortRows(rows, sortKey, sortDir), [rows, sortKey, sortDir]);
	const maxExec = useMemo(() => Math.max(1, ...rows.map((r) => r.totalExecutions)), [rows]);

	return (
		<section
			className="bg-surface-1 rounded-lg [--field-bg:var(--surface-field)]"
			aria-labelledby="breakdown-heading"
		>
			<div className="border-hairline flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
				<div>
					<h2
						id="breakdown-heading"
						className="font-heading text-foreground text-sm font-semibold"
					>
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
							'border-hairline-row text-muted-foreground hidden border-b px-4 py-2 text-[10px] font-medium',
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
								tone={toneFor(tones, row.id)}
								isAgent={lens === 'agents'}
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

/**
 * The row's avatar. Normally the plain `VendorIcon` / `AgentBadge`; when the
 * chart moved this entity off its own hue (a collision with a busier row),
 * the tile follows the series by remapping its `--avatar-{n}` tokens to the
 * assigned tone — the primitive is untouched, and the row still reads as one
 * colour end to end.
 */
function EntityAvatar({
	tone,
	label,
	isAgent,
}: {
	tone: EntityTone;
	label: string;
	isAgent: boolean;
}) {
	const avatar = isAgent ? (
		<AgentBadge id={tone.seed ?? undefined} name={label} size="sm" />
	) : (
		<VendorIcon name={label} vendor={tone.seed ?? label} size="sm" />
	);
	// The tone index the primitive will paint with (its own seed hash) …
	const painted = tone.avatarTone ?? (isAgent ? null : avatarToneIndex(label));
	// … and what the chart needs instead: the fallback hue after a collision,
	// or the neutral grey for an unattributed API bucket.
	const target =
		tone.tone != null && tone.tone !== tone.avatarTone
			? `--avatar-${tone.tone}`
			: tone.seed == null && !isAgent
				? '--avatar-neutral'
				: null;
	const remap =
		painted != null && target
			? ({
					[`--avatar-${painted}-bg`]: `var(${target}-bg)`,
					[`--avatar-${painted}-fg`]: `var(${target}-fg)`,
				} as CSSProperties)
			: undefined;
	return (
		<span
			aria-hidden="true"
			className="flex shrink-0"
			style={remap}
			data-chart-tone={tone.tone ?? 'neutral'}
		>
			{avatar}
		</span>
	);
}

function BreakdownRow({
	row,
	tone,
	isAgent,
	maxExec,
	isLast,
	href,
}: {
	row: EntityUsageRow;
	tone: EntityTone;
	isAgent: boolean;
	maxExec: number;
	isLast: boolean;
	href: string | null;
}) {
	const color = tone.fill;
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
				{/* Identity tile: the shared pastel mark (APIs) / agent badge,
				    seeded exactly as on the entity's own pages, so a row reads
				    the same here as everywhere else. The sparkline and volume
				    bar wear the same hue in its chart tone. */}
				<EntityAvatar tone={tone} label={row.label} isAgent={isAgent} />
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
		href && 'hover:bg-tint-2',
		!isLast && 'border-hairline-row border-b',
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
