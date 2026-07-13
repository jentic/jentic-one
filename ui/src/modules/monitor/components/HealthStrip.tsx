/**
 * HealthStrip — the headline pill row of the Monitor Overview, ported from
 * jentic-mini (`ui/src/components/monitor/overview/HealthStrip.tsx`).
 *
 * Parity note: jentic-mini's strip also carries a latency "Fast/Normal/Slow"
 * pill and an "N APIs active" avatar cluster. Those need `avg_ms` and per-API
 * grouping from a richer aggregation endpoint (jentic-one#561) that the current
 * `GET /monitoring/executions` doesn't provide — so this build ships the health
 * pill (which we can compute from total/success/failed) and degrades the rest.
 */
import { motion } from 'framer-motion';
import { Check, AlertTriangle, XOctagon } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { ExecutionStatsResponse } from '@/modules/monitor/api';

type HealthLevel = 'healthy' | 'degraded' | 'issues';

function getHealthLevel(successRate: number): HealthLevel {
	if (successRate >= 97) return 'healthy';
	if (successRate >= 90) return 'degraded';
	return 'issues';
}

const HEALTH_CONFIG: Record<
	HealthLevel,
	{ label: string; pill: string; icon: typeof Check; cls: string; bar: string }
> = {
	healthy: {
		label: 'All systems healthy',
		pill: 'Healthy',
		icon: Check,
		cls: 'bg-accent-green/10 text-accent-green border-accent-green/25',
		bar: 'bg-accent-green',
	},
	degraded: {
		label: 'Some issues detected',
		pill: 'Degraded',
		icon: AlertTriangle,
		cls: 'bg-accent-orange/10 text-accent-orange border-accent-orange/25',
		bar: 'bg-accent-orange',
	},
	issues: {
		label: 'Attention needed',
		pill: 'Critical',
		icon: XOctagon,
		cls: 'bg-accent-pink/10 text-accent-pink border-accent-pink/25',
		bar: 'bg-accent-pink',
	},
};

const pillVariant = {
	hidden: { opacity: 0, scale: 0.8, y: 4 },
	visible: {
		opacity: 1,
		scale: 1,
		y: 0,
		transition: { type: 'spring' as const, stiffness: 400, damping: 20 },
	},
};

const staggerContainer = {
	hidden: {},
	visible: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
} as const;

export function HealthStrip({ stats }: { stats: ExecutionStatsResponse }) {
	const successRate = stats.success_rate_percent;
	const total = stats.total_executions;
	const failures = stats.daily_buckets.reduce((sum, b) => sum + b.failed, 0);
	const health = getHealthLevel(successRate);
	const cfg = HEALTH_CONFIG[health];
	const Icon = cfg.icon;

	return (
		<motion.div
			className="flex flex-wrap items-center gap-3"
			variants={staggerContainer}
			initial="hidden"
			animate="visible"
		>
			<motion.div variants={pillVariant} className="group relative">
				<div
					className={cn(
						'flex min-h-9 items-center gap-2 rounded-full border px-3.5 py-1.5',
						cfg.cls,
					)}
				>
					<Icon className="h-3.5 w-3.5" aria-hidden="true" />
					<span className="text-xs font-semibold">{cfg.pill}</span>
					{failures > 0 && (
						<>
							<span className="text-xs opacity-40">·</span>
							<span className="text-xs font-semibold">
								{failures} {failures === 1 ? 'issue' : 'issues'}
							</span>
						</>
					)}
				</div>

				{/* Hover-card: success-rate detail (matches jentic-mini). */}
				<div className="border-border/40 bg-card/95 pointer-events-none absolute top-full left-0 z-40 mt-2.5 w-64 rounded-xl border p-3.5 opacity-0 shadow-lg backdrop-blur-md transition-all duration-150 group-hover:pointer-events-auto group-hover:opacity-100">
					<div className="space-y-3">
						<div className="flex items-center gap-2">
							<Icon className="h-3.5 w-3.5" aria-hidden="true" />
							<span className="text-foreground text-sm font-semibold">
								{cfg.label}
							</span>
						</div>
						<div className="space-y-1.5">
							<div className="flex items-center justify-between text-xs">
								<span className="text-muted-foreground">Success rate</span>
								<span className="text-foreground font-mono font-semibold">
									{successRate.toFixed(1)}%
								</span>
							</div>
							<div className="bg-muted/60 h-1.5 overflow-hidden rounded-full">
								<div
									className={cn(
										'h-full rounded-full transition-all duration-500',
										cfg.bar,
									)}
									style={{ width: `${Math.min(100, successRate)}%` }}
								/>
							</div>
						</div>
						<div className="bg-muted/30 space-y-1 rounded-lg px-2.5 py-2 text-xs">
							<div className="flex items-center justify-between">
								<span className="text-muted-foreground">Successful</span>
								<span className="text-accent-green font-mono font-semibold">
									{(total - failures).toLocaleString()}
								</span>
							</div>
							{failures > 0 && (
								<div className="flex items-center justify-between">
									<span className="text-muted-foreground">Failed</span>
									<span className="text-accent-pink font-mono font-semibold">
										{failures.toLocaleString()}
									</span>
								</div>
							)}
							<div className="border-border/20 flex items-center justify-between border-t pt-1 text-[10px]">
								<span className="text-muted-foreground">Total</span>
								<span className="text-muted-foreground font-mono">
									{total.toLocaleString()}
								</span>
							</div>
						</div>
					</div>
				</div>
			</motion.div>

			{/* TODO(#561): latency "Fast/Normal/Slow" pill + "N APIs active" avatar
			    cluster need avg_ms and per-API grouping from the enriched endpoint. */}
		</motion.div>
	);
}
