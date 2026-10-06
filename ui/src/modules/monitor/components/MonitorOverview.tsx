/**
 * MonitorOverview — the left column of Monitor's default layout: where the
 * traffic went, drawn the classic way.
 *
 *   1. Execution volume — stacked bars coloured per API / agent
 *   2. Bubble chart     — the same entities sized by traffic
 *   3. Breakdown        — the top rows, sortable, drilling into API calls
 *
 * Every chart shares one palette indexed by busiest-first order, so an API
 * keeps its colour across all three. Fed by {@link useUsageOverview}; the
 * page owns the query so the stat strip above reads the same data.
 *
 * All-zero data swaps the charts for an EmptyState with guidance rather than
 * empty axes.
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import { BarChart3 } from 'lucide-react';
import { EmptyState, ErrorAlert, LoadingState } from '@/shared/ui';
import { RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE } from '@/shared/lib';
import { UsageCharts } from '@/modules/monitor/components/UsageCharts';
import { UsageBubbleChart } from '@/modules/monitor/components/UsageBubbleChart';
import { UsageBreakdown } from '@/modules/monitor/components/UsageBreakdown';
import { monitorHref } from '@/modules/monitor/lib/links';
import type { UsageLens } from '@/modules/monitor/lib/palette';
import type { EntityUsageRow } from '@/modules/monitor/lib/usage';
import type { UsageOverviewState } from '@/modules/monitor/lib/useUsageOverview';

const staggerContainer = {
	hidden: {},
	show: { transition: { staggerChildren: 0.08 } },
};
const chartVariant = {
	hidden: { opacity: 0, y: 12 },
	show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 260, damping: 24 } },
} as const;

export interface LinkBase {
	days?: number;
	actorId?: string;
	actorType?: string;
}

/**
 * Drill-down target for a breakdown row. APIs filter API calls by the
 * colon-encoded `api` param (`vendor:name`); agents by actor (the usage key is
 * `actor_type/actor_id`). The Unattributed bucket has nothing to filter on, and retired service-account
 * rows stay static (there's no live actor behind them).
 */
export function breakdownRowHref(
	lens: UsageLens,
	row: EntityUsageRow,
	base: LinkBase,
): string | null {
	if (row.id === '__unattributed__' || row.label === 'Unattributed') return null;
	if (lens === 'agents') {
		const slash = row.id.indexOf('/');
		if (slash < 0 || slash === row.id.length - 1) return null;
		if (row.id.slice(0, slash) === RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE) return null;
		return monitorHref({
			show: 'calls',
			days: base.days,
			actorType: row.id.slice(0, slash),
			actorId: row.id.slice(slash + 1),
		});
	}
	if (lens === 'apis') {
		const href = monitorHref({ show: 'calls', ...base });
		const sep = href.includes('?') ? '&' : '?';
		return `${href}${sep}api=${encodeURIComponent(row.id.replace('/', ':'))}`;
	}
	return null;
}

export function MonitorOverview({
	state,
	linkBase,
	hasActor,
}: {
	state: UsageOverviewState;
	linkBase: LinkBase;
	hasActor: boolean;
}) {
	const [lens, setLens] = useState<UsageLens>('apis');
	const { usage, overview, apis, agents, days } = state;

	if (state.isLoading) return <LoadingState />;

	if (state.error) {
		return (
			<ErrorAlert
				message={
					state.error instanceof Error ? state.error : 'Failed to load usage statistics.'
				}
				onRetry={state.retry}
				retrying={state.isFetching}
			/>
		);
	}

	if (!usage || !overview || overview.totalExecutions === 0) {
		return (
			<EmptyState
				icon={<BarChart3 className="h-8 w-8" />}
				title="No executions yet"
				description={`No API calls were recorded in the last ${days === 1 ? '24 hours' : `${days} days`}${hasActor ? ' for this actor' : ''}. Once agents start running operations, usage trends and per-API activity will appear here.`}
			/>
		);
	}

	const rows = lens === 'apis' ? apis : agents;

	return (
		<motion.div
			key={`overview-${days}-${linkBase.actorId ?? ''}`}
			variants={staggerContainer}
			initial="hidden"
			animate="show"
			className="min-w-0 space-y-4"
		>
			<motion.div variants={chartVariant}>
				<UsageCharts usage={usage} apis={apis} agents={agents} />
			</motion.div>
			<motion.div variants={chartVariant}>
				<UsageBubbleChart apis={apis} agents={agents} />
			</motion.div>
			<motion.div variants={chartVariant}>
				<UsageBreakdown
					lens={lens}
					onLensChange={setLens}
					rows={rows}
					rowHref={(row) => breakdownRowHref(lens, row, linkBase)}
				/>
			</motion.div>
		</motion.div>
	);
}
