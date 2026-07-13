/**
 * "Breakdown" table — ported from jentic-mini's BreakdownSection look (icon
 * tile, name + sublabel, health dot, volume bar). jentic-mini toggles between
 * APIs / Toolkits / Agents and shows a latency "Speed" column + sparkline
 * "Trend"; those need per-group aggregation, per-row avg_ms, and trend[] from
 * the enriched endpoint (jentic-one#561). Until then this renders the busiest
 * operations (the only grouping the endpoint exposes) with the columns we can
 * compute: Health (success rate) and Volume.
 */
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';
import type { TopOperation } from '@/modules/monitor/api';

const PALETTE = ['#6366f1', '#8b5cf6', '#0ea5e9', '#14b8a6', '#f59e0b', '#ec4899', '#10b981'];

function initials(name: string): string {
	const words = name
		.replace(/api/gi, '')
		.trim()
		.split(/[\s-_/]+/)
		.filter(Boolean);
	return (words[0]?.[0] ?? '?').concat(words[1]?.[0] ?? '').toUpperCase();
}

function healthDot(successRate: number): { color: string; label: string } {
	if (successRate >= 97) return { color: 'bg-accent-green', label: 'Healthy' };
	if (successRate >= 90) return { color: 'bg-accent-amber', label: 'Degraded' };
	return { color: 'bg-accent-pink', label: 'Issues' };
}

interface Row {
	id: string;
	label: string;
	subLabel: string;
	total: number;
	successRate: number;
	color: string;
}

export function TopOperations({ operations }: { operations: TopOperation[] }) {
	const rows: Row[] = useMemo(
		() =>
			[...operations]
				.sort((a, b) => b.total - a.total)
				.map((op, i) => ({
					id: op.operation_id + i,
					label: op.operation_id,
					subLabel: `${op.api_vendor} · ${op.api_name}`,
					total: op.total,
					successRate: op.total > 0 ? ((op.total - op.failed) / op.total) * 100 : 100,
					color: PALETTE[i % PALETTE.length],
				})),
		[operations],
	);
	const maxExec = useMemo(() => Math.max(1, ...rows.map((r) => r.total)), [rows]);

	return (
		<div className="border-border bg-card rounded-xl border">
			<div className="border-border flex items-center justify-between border-b px-4 py-3">
				<div>
					<h2 className="text-foreground text-sm font-semibold">Breakdown</h2>
					<p className="text-muted-foreground text-xs">Busiest operations</p>
				</div>
			</div>

			<div className="border-border/50 text-muted-foreground hidden items-center gap-3 border-b px-4 py-2 text-[10px] font-medium tracking-wider uppercase sm:grid sm:grid-cols-[1fr_56px_140px]">
				<span>Name</span>
				<span className="text-center">Health</span>
				<span>Volume</span>
			</div>

			{rows.length === 0 ? (
				<p className="text-muted-foreground py-12 text-center text-sm">
					No operation data available
				</p>
			) : (
				rows.map((row, i) => {
					const health = healthDot(row.successRate);
					const ratio = row.total / maxExec;
					return (
						<motion.div
							key={row.id}
							initial={{ opacity: 0, y: 8 }}
							animate={{ opacity: 1, y: 0 }}
							transition={{ delay: i * 0.04, duration: 0.25 }}
							className={cn(
								'group hover:bg-muted/30 px-4 py-2.5 transition-colors',
								'sm:grid sm:grid-cols-[1fr_56px_140px] sm:items-center sm:gap-3',
								i !== rows.length - 1 && 'border-border/30 border-b',
							)}
						>
							<div className="flex min-w-0 items-center gap-2.5">
								<div
									className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[10px] font-bold text-white"
									style={{ backgroundColor: row.color }}
								>
									{initials(row.subLabel.split(' · ')[0])}
								</div>
								<div className="min-w-0 flex-1">
									<p className="text-foreground truncate font-mono text-xs font-medium">
										{row.label}
									</p>
									<p className="text-muted-foreground truncate text-[10px]">
										{row.subLabel}
									</p>
								</div>
							</div>

							<div
								className="mt-2 flex items-center gap-1.5 sm:mt-0 sm:justify-center"
								title={`${row.successRate.toFixed(0)}% success — ${health.label}`}
							>
								<span className={cn('h-2 w-2 rounded-full', health.color)} />
								<span className="text-muted-foreground text-[11px] tabular-nums">
									{row.successRate.toFixed(0)}%
								</span>
							</div>

							<div className="mt-1.5 flex flex-col gap-0.5 sm:mt-0">
								<div className="bg-muted/50 h-1.5 w-full rounded-full">
									<div
										className="h-full rounded-full transition-all duration-500"
										style={{
											width: `${Math.max(4, ratio * 100)}%`,
											backgroundColor: row.color,
										}}
									/>
								</div>
								<span className="text-muted-foreground text-[10px] tabular-nums">
									{row.total.toLocaleString()} calls
								</span>
							</div>
						</motion.div>
					);
				})
			)}
		</div>
	);
}
