import { type JSX } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { HealthStrip } from './HealthStrip';
import { ApiDailyBarChart } from './ApiDailyBarChart';
import { ApiBubbleChart } from './ApiBubbleChart';
import { BreakdownSection } from './BreakdownSection';
import type {
	AgentUsageSummary,
	ApiUsageSummary,
	MonitorStats,
	TimelinePoint,
	TimeRange,
	ToolkitUsageSummary,
} from '@/components/monitor/types';

const staggerContainer = {
	hidden: {},
	visible: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
} as const;

const chartVariant = {
	hidden: { opacity: 0, y: 20 },
	visible: {
		opacity: 1,
		y: 0,
		transition: { type: 'spring' as const, stiffness: 200, damping: 24 },
	},
};

const fadeScale = {
	hidden: { opacity: 0, scale: 0.96 },
	visible: { opacity: 1, scale: 1, transition: { duration: 0.35 } },
	exit: { opacity: 0, scale: 0.96, transition: { duration: 0.2 } },
};

interface OverviewTabProps {
	stats: MonitorStats | null;
	rawTimelinePoints: TimelinePoint[];
	timeRange: TimeRange;
	apiUsage: ApiUsageSummary[];
	toolkitUsage: ToolkitUsageSummary[];
	agentUsage: AgentUsageSummary[];
	isLoading: boolean;
}

export function OverviewTab({
	stats,
	rawTimelinePoints,
	timeRange,
	apiUsage,
	toolkitUsage,
	agentUsage,
	isLoading,
}: OverviewTabProps): JSX.Element {
	return (
		<AnimatePresence mode="wait">
			{isLoading && !stats ? (
				<motion.div
					key="skeleton"
					className="space-y-4"
					initial={{ opacity: 0 }}
					animate={{ opacity: 1 }}
					exit={{ opacity: 0 }}
				>
					<div className="bg-muted/60 h-9 w-72 animate-pulse rounded-full" />
					<div className="border-border bg-card h-[460px] animate-pulse rounded-xl border" />
					<div className="border-border bg-card h-[480px] animate-pulse rounded-xl border" />
					<div className="border-border bg-card h-[420px] animate-pulse rounded-xl border" />
				</motion.div>
			) : !stats ? (
				<motion.div
					key="empty"
					className="flex flex-col items-center justify-center py-16"
					variants={fadeScale}
					initial="hidden"
					animate="visible"
					exit="exit"
				>
					<p className="text-muted-foreground">No data available</p>
				</motion.div>
			) : (
				<motion.div
					key={`content-${timeRange}`}
					className="space-y-4"
					variants={staggerContainer}
					initial="hidden"
					animate="visible"
				>
					<HealthStrip stats={stats} apiUsage={apiUsage} />

					<motion.div variants={chartVariant}>
						<ApiDailyBarChart points={rawTimelinePoints} timeRange={timeRange} />
					</motion.div>

					<motion.div variants={chartVariant}>
						<ApiBubbleChart
							apis={apiUsage}
							toolkits={toolkitUsage}
							agents={agentUsage}
						/>
					</motion.div>

					<motion.div variants={chartVariant}>
						<BreakdownSection
							apis={apiUsage}
							toolkits={toolkitUsage}
							agents={agentUsage}
						/>
					</motion.div>
				</motion.div>
			)}
		</AnimatePresence>
	);
}
