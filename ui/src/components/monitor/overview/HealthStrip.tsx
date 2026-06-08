import { type JSX } from 'react';
import { motion } from 'framer-motion';
import { Check, AlertTriangle, XOctagon, Gauge } from 'lucide-react';
import type { MonitorStats, ApiUsageSummary } from '@/components/monitor/types';
import { getVendorConfig, getInitials } from '@/components/monitor/shared/vendor-icons';
import { formatLatency } from '@/components/monitor/shared';
import { cn } from '@/lib/utils';

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

const HEALTH_CONFIG: Record<HealthLevel, { label: string; icon: JSX.Element; cls: string }> = {
	healthy: {
		label: 'All systems healthy',
		icon: <Check className="h-3.5 w-3.5" />,
		cls: 'bg-accent-green/8 text-accent-green border-accent-green/25',
	},
	degraded: {
		label: 'Some issues detected',
		icon: <AlertTriangle className="h-3.5 w-3.5" />,
		cls: 'bg-accent-orange/8 text-accent-orange border-accent-orange/25',
	},
	issues: {
		label: 'Attention needed',
		icon: <XOctagon className="h-3.5 w-3.5" />,
		cls: 'bg-accent-pink/8 text-accent-pink border-accent-pink/25',
	},
};

const SPEED_CONFIG: Record<SpeedLevel, { label: string; cls: string }> = {
	fast: { label: 'Fast', cls: 'text-accent-green' },
	normal: { label: 'Normal', cls: 'text-accent-orange' },
	slow: { label: 'Slow', cls: 'text-accent-pink' },
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

interface HealthStripProps {
	stats: MonitorStats;
	apiUsage: ApiUsageSummary[];
}

export function HealthStrip({ stats, apiUsage }: HealthStripProps): JSX.Element {
	const health = getHealthLevel(stats.successRate);
	const hCfg = HEALTH_CONFIG[health];
	const speed = getSpeedLevel(stats.avgLatencyMs);
	const sCfg = SPEED_CONFIG[speed];
	const failures = stats.failureCount;
	const activeApis = apiUsage.filter((a) => a.totalExecutions > 0).slice(0, 6);

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
								'flex min-h-9 items-center gap-2 rounded-full border px-3.5 py-1.5 backdrop-blur-sm',
								hCfg.cls,
							)}
						>
							{hCfg.icon}
							<span className="text-xs font-semibold">
								{health === 'healthy'
									? 'Healthy'
									: health === 'degraded'
										? 'Degraded'
										: 'Critical'}
							</span>
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
					<HealthDetail stats={stats} health={health} hCfg={hCfg} failures={failures} />
				</HoverCard>
			</motion.div>

			<motion.div variants={pillVariant}>
				<HoverCard
					trigger={
						<div className="border-border/60 bg-card flex min-h-9 items-center gap-1.5 rounded-full border px-3.5 py-1.5">
							<Gauge className={cn('h-3.5 w-3.5', sCfg.cls)} />
							<span className={cn('text-xs font-semibold', sCfg.cls)}>
								{sCfg.label}
							</span>
							<span className="text-muted-foreground text-[10px] font-medium">
								response
							</span>
						</div>
					}
				>
					<SpeedDetail stats={stats} speed={speed} sCfg={sCfg} />
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
									{activeApis.length} APIs active
								</span>
								<div className="flex -space-x-1.5">
									{activeApis.map((api) => {
										const config = getVendorConfig(api.vendor);
										return (
											<div
												key={api.vendor}
												className="border-background flex h-6 w-6 items-center justify-center overflow-hidden rounded-full border-2"
												style={{ backgroundColor: config.bg }}
											>
												{config.iconUrl ? (
													<img
														src={config.iconUrl}
														alt={api.apiName}
														className="h-3 w-3 object-contain"
														style={{
															filter:
																config.text === '#fff'
																	? 'invert(1)'
																	: undefined,
														}}
													/>
												) : (
													<span
														className="text-[7px] font-bold"
														style={{ color: config.text }}
													>
														{getInitials(api.apiName)}
													</span>
												)}
											</div>
										);
									})}
								</div>
							</div>
						}
					>
						<ActiveApisDetail apis={activeApis} />
					</HoverCard>
				</motion.div>
			)}
		</motion.div>
	);
}

function HoverCard({
	trigger,
	children,
	align = 'left',
}: {
	trigger: JSX.Element;
	children: JSX.Element;
	align?: 'left' | 'right';
}): JSX.Element {
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

function HealthDetail({
	stats,
	health,
	hCfg,
	failures,
}: {
	stats: MonitorStats;
	health: HealthLevel;
	hCfg: { label: string; icon: JSX.Element; cls: string };
	failures: number;
}): JSX.Element {
	return (
		<div className="space-y-3">
			<div className="flex items-center gap-2">
				{hCfg.icon}
				<span className="text-foreground text-sm font-semibold">{hCfg.label}</span>
			</div>
			<div className="space-y-1.5">
				<div className="flex items-center justify-between text-xs">
					<span className="text-muted-foreground">Success rate</span>
					<span className="text-foreground font-mono font-semibold">
						{stats.successRate.toFixed(1)}%
					</span>
				</div>
				<div className="bg-muted/60 h-1.5 overflow-hidden rounded-full">
					<div
						className={cn(
							'h-full rounded-full transition-all duration-500',
							health === 'healthy' && 'bg-accent-green',
							health === 'degraded' && 'bg-accent-orange',
							health === 'issues' && 'bg-accent-pink',
						)}
						style={{ width: `${Math.min(100, stats.successRate)}%` }}
					/>
				</div>
			</div>
			<div className="bg-muted/30 space-y-1 rounded-lg px-2.5 py-2 text-xs">
				<div className="flex items-center justify-between">
					<span className="text-muted-foreground">Successful</span>
					<span className="text-accent-green font-mono font-semibold">
						{(stats.totalExecutions - failures).toLocaleString()}
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
						{stats.totalExecutions.toLocaleString()}
					</span>
				</div>
			</div>
		</div>
	);
}

function SpeedDetail({
	stats,
	speed,
	sCfg,
}: {
	stats: MonitorStats;
	speed: SpeedLevel;
	sCfg: { label: string; cls: string };
}): JSX.Element {
	return (
		<div className="space-y-3">
			<div className="flex items-center gap-2">
				<Gauge className={cn('h-4 w-4', sCfg.cls)} />
				<span className="text-foreground text-sm font-semibold">
					{sCfg.label} response time
				</span>
			</div>
			<div className="space-y-2 text-xs">
				<div className="flex items-center justify-between">
					<span className="text-muted-foreground">Average latency</span>
					<span className={cn('font-mono font-semibold', sCfg.cls)}>
						{formatLatency(stats.avgLatencyMs)}
					</span>
				</div>
				<div className="flex items-center gap-1">
					{(['fast', 'normal', 'slow'] as const).map((tier) => (
						<div
							key={tier}
							className={cn(
								'h-1.5 flex-1 rounded-full',
								speed === tier
									? tier === 'fast'
										? 'bg-accent-green'
										: tier === 'normal'
											? 'bg-accent-orange'
											: 'bg-accent-pink'
									: 'bg-muted/60',
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
	);
}

function ActiveApisDetail({ apis }: { apis: ApiUsageSummary[] }): JSX.Element {
	return (
		<div className="space-y-3">
			<p className="text-foreground text-sm font-semibold">{apis.length} APIs active</p>
			<div className="space-y-2">
				{apis.map((api) => {
					const config = getVendorConfig(api.vendor);
					return (
						<div key={api.vendor} className="flex items-center gap-2.5">
							<div
								className="flex h-5 w-5 flex-shrink-0 items-center justify-center overflow-hidden rounded-md"
								style={{ backgroundColor: config.bg }}
							>
								{config.iconUrl ? (
									<img
										src={config.iconUrl}
										alt={api.apiName}
										className="h-3 w-3 object-contain"
										style={{
											filter:
												config.text === '#fff' ? 'invert(1)' : undefined,
										}}
									/>
								) : (
									<span
										className="text-[6px] font-bold"
										style={{ color: config.text }}
									>
										{getInitials(api.apiName)}
									</span>
								)}
							</div>
							<span className="text-foreground flex-1 truncate text-xs">
								{api.apiName}
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
					);
				})}
			</div>
		</div>
	);
}
