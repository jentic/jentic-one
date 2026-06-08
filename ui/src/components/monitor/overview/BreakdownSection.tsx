import { type JSX, useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type {
	AgentUsageSummary,
	ApiUsageSummary,
	ToolkitUsageSummary,
} from '@/components/monitor/types';
import { getVendorConfig, getInitials } from '@/components/monitor/shared/vendor-icons';
import { SparklineChart, formatLatency, formatPercent } from '@/components/monitor/shared';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { cn } from '@/lib/utils';

type BreakdownMode = 'apis' | 'toolkits' | 'agents';

interface BreakdownSectionProps {
	apis: ApiUsageSummary[];
	toolkits: ToolkitUsageSummary[];
	agents: AgentUsageSummary[];
}

interface RowItem {
	id: string;
	label: string;
	subLabel?: string;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	trend: number[];
	color: string;
	textColor: string;
	iconUrl?: string;
	tags: Array<{ key: string; label: string }>;
}

const TOOLKIT_PALETTE = [
	'#6366f1',
	'#8b5cf6',
	'#0ea5e9',
	'#14b8a6',
	'#f59e0b',
	'#ef4444',
	'#ec4899',
	'#10b981',
];

const AGENT_PALETTE = ['#0891b2', '#7c3aed', '#db2777', '#16a34a', '#ea580c', '#475569'];

function getHealthDot(successRate: number): { color: string; label: string } {
	if (successRate >= 97) return { color: 'bg-accent-green', label: 'Healthy' };
	if (successRate >= 90) return { color: 'bg-accent-amber', label: 'Degraded' };
	return { color: 'bg-accent-red', label: 'Issues' };
}

function VolumeBar({ ratio, color }: { ratio: number; color: string }): JSX.Element {
	return (
		<div className="bg-muted/50 h-1.5 w-full rounded-full">
			<div
				className="h-full rounded-full transition-all duration-500"
				style={{ width: `${Math.max(4, ratio * 100)}%`, backgroundColor: color }}
			/>
		</div>
	);
}

export function BreakdownSection({ apis, toolkits, agents }: BreakdownSectionProps): JSX.Element {
	const [mode, setMode] = useState<BreakdownMode>('apis');

	const isEmpty =
		mode === 'apis'
			? apis.length === 0
			: mode === 'toolkits'
				? toolkits.length === 0
				: agents.length === 0;

	const rows: RowItem[] = useMemo(() => {
		if (mode === 'apis') {
			return [...apis]
				.sort((a, b) => b.totalExecutions - a.totalExecutions)
				.map((api) => {
					const cfg = getVendorConfig(api.vendor);
					return {
						id: `${api.vendor}:${api.apiName}`,
						label: api.apiName,
						subLabel: api.apiVersion,
						totalExecutions: api.totalExecutions,
						successRate: api.successRate,
						avgLatencyMs: api.avgLatencyMs,
						trend: api.recentTrend,
						color: cfg.bg,
						textColor: cfg.text,
						iconUrl: cfg.iconUrl,
						tags: [],
					};
				});
		}
		if (mode === 'agents') {
			return [...agents]
				.sort((a, b) => b.totalExecutions - a.totalExecutions)
				.map((agent, i) => ({
					id: agent.agentId,
					label: agent.agentName,
					subLabel: undefined,
					totalExecutions: agent.totalExecutions,
					successRate: agent.successRate,
					avgLatencyMs: agent.avgLatencyMs,
					trend: agent.recentTrend,
					color: AGENT_PALETTE[i % AGENT_PALETTE.length],
					textColor: '#fff',
					tags: [],
				}));
		}
		return [...toolkits]
			.sort((a, b) => b.totalExecutions - a.totalExecutions)
			.map((toolkit, i) => ({
				id: toolkit.toolkitId,
				label: toolkit.toolkitName,
				subLabel: toolkit.toolkitMode ?? undefined,
				totalExecutions: toolkit.totalExecutions,
				successRate: toolkit.successRate,
				avgLatencyMs: toolkit.avgLatencyMs,
				trend: toolkit.recentTrend,
				color: TOOLKIT_PALETTE[i % TOOLKIT_PALETTE.length],
				textColor: '#fff',
				tags: toolkit.topApis.slice(0, 3).map((a) => ({
					key: `${a.vendor}:${a.apiName}`,
					label: a.apiName,
				})),
			}));
	}, [mode, apis, toolkits, agents]);

	const maxExec = useMemo(() => Math.max(1, ...rows.map((r) => r.totalExecutions)), [rows]);

	const headerSubtitle =
		mode === 'apis'
			? 'Performance for each connected API'
			: mode === 'agents'
				? 'Activity per agent identity'
				: 'Activity for each toolkit';

	return (
		<div className="border-border bg-card rounded-xl border">
			<div className="border-border flex items-center justify-between border-b px-4 py-3">
				<div>
					<h2 className="text-foreground text-sm font-semibold">Breakdown</h2>
					<p className="text-muted-foreground text-xs">{headerSubtitle}</p>
				</div>
				<SegmentedToggle
					layoutId="breakdownToggle"
					options={[
						{ value: 'apis', label: 'APIs' },
						{ value: 'toolkits', label: 'Toolkits' },
						{ value: 'agents', label: 'Agents' },
					]}
					value={mode}
					onChange={setMode}
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
				{isEmpty ? (
					<motion.div
						key={`empty-${mode}`}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						className="flex items-center justify-center py-12"
					>
						<p className="text-muted-foreground text-sm">
							No {mode === 'apis' ? 'API' : mode === 'agents' ? 'agent' : 'toolkit'}{' '}
							data available
						</p>
					</motion.div>
				) : (
					<motion.div
						key={mode}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: 0.2 }}
					>
						{rows.map((row, i) => (
							<BreakdownRow
								key={row.id}
								row={row}
								maxExec={maxExec}
								mode={mode}
								index={i}
								isLast={i === rows.length - 1}
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
	maxExec,
	mode,
	index,
	isLast,
}: {
	row: RowItem;
	maxExec: number;
	mode: BreakdownMode;
	index: number;
	isLast: boolean;
}): JSX.Element {
	const health = getHealthDot(row.successRate);
	const ratio = row.totalExecutions / maxExec;

	const latencyClass = cn(
		'text-xs tabular-nums font-medium',
		row.avgLatencyMs <= 300
			? 'text-accent-green'
			: row.avgLatencyMs <= 800
				? 'text-foreground'
				: 'text-accent-red',
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
					className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg"
					style={{ backgroundColor: row.color }}
				>
					{row.iconUrl ? (
						<img
							src={row.iconUrl}
							alt={row.label}
							className="h-5 w-5 object-contain"
							style={{ filter: row.textColor === '#fff' ? 'invert(1)' : undefined }}
						/>
					) : (
						<span className="text-[10px] font-bold" style={{ color: row.textColor }}>
							{getInitials(row.label)}
						</span>
					)}
				</div>
				<div className="min-w-0 flex-1">
					<div className="flex items-center gap-1.5">
						<span className="text-foreground truncate text-sm font-medium">
							{row.label}
						</span>
						{row.subLabel && (
							<span className="text-muted-foreground shrink-0 text-[10px]">
								{row.subLabel}
							</span>
						)}
					</div>
					{row.tags.length > 0 && (
						<div className="mt-0.5 flex items-center gap-1 overflow-hidden">
							<span className="text-muted-foreground shrink-0 text-[10px]">
								{mode === 'apis' ? 'by' : 'uses'}
							</span>
							{row.tags.map((tag) => (
								<span
									key={tag.key}
									className="bg-muted/60 text-muted-foreground truncate rounded px-1 py-px text-[10px]"
								>
									{tag.label}
								</span>
							))}
						</div>
					)}
				</div>
				<span className={cn('shrink-0 sm:hidden', latencyClass)}>
					{formatLatency(row.avgLatencyMs)}
				</span>
			</div>

			<div className="mt-2 flex items-center gap-3 sm:hidden">
				<SparklineChart
					data={row.trend}
					width={56}
					height={18}
					strokeWidth={1.5}
					color={row.color}
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
				<VolumeBar ratio={ratio} color={row.color} />
			</div>

			<div className="hidden items-center justify-center sm:flex">
				<SparklineChart
					data={row.trend}
					width={56}
					height={20}
					strokeWidth={1.5}
					color={row.color}
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
				<VolumeBar ratio={ratio} color={row.color} />
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
