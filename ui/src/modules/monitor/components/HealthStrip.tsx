/**
 * HealthStrip — the headline pill row of the Monitor Overview, ported from
 * jentic-mini (`ui/src/components/monitor/overview/HealthStrip.tsx`).
 *
 * Full parity build (jentic-one-internal#561): health pill with success-rate
 * hover detail, latency "Fast/Normal/Slow" pill with avg/p50/p95 hover
 * detail, and the "N APIs active" avatar cluster (initials tiles — jentic-one
 * has no vendor-icon registry).
 */
import { motion } from 'framer-motion';
import { Check, AlertTriangle, XOctagon, Gauge } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { formatLatency } from '@/modules/monitor/lib/format';
import { API_PALETTE, getInitials, textColor } from '@/modules/monitor/lib/palette';
import type { EntityUsageRow, UsageOverview } from '@/modules/monitor/lib/usage';

type HealthLevel = 'healthy' | 'degraded' | 'issues';
type SpeedLevel = 'fast' | 'normal' | 'slow';

function getHealthLevel(successRate: number): HealthLevel {
	if (successRate >= 97) return 'healthy';
	if (successRate >= 90) return 'degraded';
	return 'issues';
}

function getSpeedLevel(avgMs: number): SpeedLevel {
	if (avgMs <= 300) return 'fast';
	if (avgMs <= 800) return 'normal';
	return 'slow';
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

const SPEED_CONFIG: Record<SpeedLevel, { label: string; cls: string; bar: string }> = {
	fast: { label: 'Fast', cls: 'text-accent-green', bar: 'bg-accent-green' },
	normal: { label: 'Normal', cls: 'text-accent-orange', bar: 'bg-accent-orange' },
	slow: { label: 'Slow', cls: 'text-accent-pink', bar: 'bg-accent-pink' },
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

function HoverCard({
	trigger,
	children,
	align = 'left',
}: {
	trigger: React.ReactNode;
	children: React.ReactNode;
	align?: 'left' | 'right';
}) {
	return (
		<div className="group relative">
			{trigger}
			<div
				className={cn(
					'border-border/40 bg-card/95 pointer-events-none absolute top-full z-40 mt-2.5 w-64 rounded-xl border p-3.5 opacity-0 shadow-lg backdrop-blur-md transition-all duration-150 group-hover:pointer-events-auto group-hover:opacity-100',
					align === 'right' ? 'right-0' : 'left-0',
				)}
			>
				{children}
			</div>
		</div>
	);
}

export function HealthStrip({
	overview,
	apis,
}: {
	overview: UsageOverview;
	apis: EntityUsageRow[];
}) {
	const health = getHealthLevel(overview.successRate);
	const cfg = HEALTH_CONFIG[health];
	const Icon = cfg.icon;
	const speed = getSpeedLevel(overview.avgLatencyMs);
	const sCfg = SPEED_CONFIG[speed];
	const failures = overview.failureCount;
	// Count active APIs before slicing — the slice is only for the avatar row,
	// but the pill text must report the real count (with TOP_LIMIT 12, a
	// post-slice count would cap at "6 APIs active" forever).
	const activeApiRows = apis.filter((a) => a.totalExecutions > 0);
	const activeApis = activeApiRows.slice(0, 6);

	return (
		<motion.div
			className="flex flex-wrap items-center gap-3"
			variants={staggerContainer}
			initial="hidden"
			animate="visible"
		>
			<motion.div variants={pillVariant}>
				<HoverCard
					trigger={
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
					}
				>
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
									{overview.successRate.toFixed(1)}%
								</span>
							</div>
							<div className="bg-muted/60 h-1.5 overflow-hidden rounded-full">
								<div
									className={cn(
										'h-full rounded-full transition-all duration-500',
										cfg.bar,
									)}
									style={{ width: `${Math.min(100, overview.successRate)}%` }}
								/>
							</div>
						</div>
						<div className="bg-muted/30 space-y-1 rounded-lg px-2.5 py-2 text-xs">
							<div className="flex items-center justify-between">
								<span className="text-muted-foreground">Successful</span>
								<span className="text-accent-green font-mono font-semibold">
									{overview.successCount.toLocaleString()}
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
									{overview.totalExecutions.toLocaleString()}
								</span>
							</div>
						</div>
					</div>
				</HoverCard>
			</motion.div>

			<motion.div variants={pillVariant}>
				<HoverCard
					trigger={
						<div className="border-border/60 bg-card flex min-h-9 items-center gap-1.5 rounded-full border px-3.5 py-1.5">
							<Gauge className={cn('h-3.5 w-3.5', sCfg.cls)} aria-hidden="true" />
							<span className={cn('text-xs font-semibold', sCfg.cls)}>
								{sCfg.label}
							</span>
							<span className="text-muted-foreground text-[10px] font-medium">
								response
							</span>
						</div>
					}
				>
					<div className="space-y-3">
						<div className="flex items-center gap-2">
							<Gauge className={cn('h-4 w-4', sCfg.cls)} aria-hidden="true" />
							<span className="text-foreground text-sm font-semibold">
								{sCfg.label} response time
							</span>
						</div>
						<div className="space-y-2 text-xs">
							<div className="flex items-center justify-between">
								<span className="text-muted-foreground">Average latency</span>
								<span className={cn('font-mono font-semibold', sCfg.cls)}>
									{formatLatency(overview.avgLatencyMs)}
								</span>
							</div>
							{overview.p50Ms != null && (
								<div className="flex items-center justify-between">
									<span className="text-muted-foreground">Median (p50)</span>
									<span className="text-foreground font-mono">
										{formatLatency(overview.p50Ms)}
									</span>
								</div>
							)}
							{overview.p95Ms != null && (
								<div className="flex items-center justify-between">
									<span className="text-muted-foreground">p95</span>
									<span className="text-foreground font-mono">
										{formatLatency(overview.p95Ms)}
									</span>
								</div>
							)}
							<div className="flex items-center gap-1">
								{(['fast', 'normal', 'slow'] as const).map((tier) => (
									<div
										key={tier}
										className={cn(
											'h-1.5 flex-1 rounded-full',
											speed === tier ? SPEED_CONFIG[tier].bar : 'bg-muted/60',
										)}
									/>
								))}
							</div>
							<div className="text-muted-foreground flex items-center justify-between text-[10px]">
								<span>Fast</span>
								<span>Normal</span>
								<span>Slow</span>
							</div>
						</div>
					</div>
				</HoverCard>
			</motion.div>

			<div className="flex-1" />

			{activeApis.length > 0 && (
				<motion.div variants={pillVariant}>
					<HoverCard
						align="right"
						trigger={
							<div className="border-border/60 bg-card flex min-h-9 items-center gap-2.5 rounded-full border px-3.5 py-1.5">
								<span className="text-muted-foreground text-[11px] font-medium">
									{activeApiRows.length} APIs active
								</span>
								<div className="flex -space-x-1.5">
									{activeApis.map((api, i) => (
										<div
											key={api.id}
											className="border-background flex h-6 w-6 items-center justify-center rounded-full border-2 text-[7px] font-bold"
											style={{
												backgroundColor:
													API_PALETTE[i % API_PALETTE.length],
												color: textColor(
													API_PALETTE[i % API_PALETTE.length],
												),
											}}
											title={api.label}
										>
											{getInitials(api.label)}
										</div>
									))}
								</div>
							</div>
						}
					>
						<div className="space-y-3">
							<p className="text-foreground text-sm font-semibold">
								{activeApiRows.length} APIs active
							</p>
							<div className="space-y-2">
								{activeApis.map((api, i) => (
									<div key={api.id} className="flex items-center gap-2.5">
										<div
											className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md text-[6px] font-bold"
											style={{
												backgroundColor:
													API_PALETTE[i % API_PALETTE.length],
												color: textColor(
													API_PALETTE[i % API_PALETTE.length],
												),
											}}
										>
											{getInitials(api.label)}
										</div>
										<span className="text-foreground flex-1 truncate text-xs">
											{api.label}
										</span>
										<span
											className={cn(
												'shrink-0 font-mono text-[10px] font-semibold',
												api.successRate >= 97
													? 'text-accent-green'
													: api.successRate >= 90
														? 'text-accent-orange'
														: 'text-accent-pink',
											)}
										>
											{api.successRate.toFixed(0)}%
										</span>
									</div>
								))}
							</div>
						</div>
					</HoverCard>
				</motion.div>
			)}
		</motion.div>
	);
}
