import { PageShell, PageHeader, PageHelp, RefreshButton } from '@/shared/ui';
import { useQueryClient } from '@tanstack/react-query';
import { dashboardKeys, useHasAgents, useRecentExecutions } from '@/modules/dashboard/api';
import { GatewayHealthSection } from '@/modules/dashboard/components/GatewayHealthSection';
import { ActionInboxBell } from '@/modules/dashboard/components/ActionInboxBell';
import { RecentActivityCard } from '@/modules/dashboard/components/RecentActivityCard';
import { QuickActionsMenu } from '@/modules/dashboard/components/QuickActionsMenu';
import { FirstRunChecklist } from '@/modules/dashboard/components/FirstRunChecklist';
import { ROUTES } from '@/shared/app/routes';

/**
 * Dashboard — the `/app` index (landing) page, laid out in layers ordered by
 * how urgently an operator needs each answer:
 *
 *   1. STATUS / ACTION — the "Needs your action" bell in the page header: one
 *      count badge (red when something severe is failing) over a dropdown
 *      merging the approval queues + actionable alerts into a single
 *      urgency-sorted triage list. It lives in the header rather than the body
 *      because the Agent rail already streams the same items live — the bell
 *      is the durable, glanceable counterpart, not a second feed. Its three
 *      sources stay independent queries, so one failing endpoint degrades to
 *      an inline row instead of killing the queue.
 *   2. PERFORMANCE — real gateway KPIs and trend charts from the org:admin
 *      `GET /monitoring/usage` aggregate (no more client-side approximations).
 *   3. CONTEXT — top APIs / toolkits / agents by usage, same query.
 *   4. DETAIL — a five-row recent-activity teaser that links into Monitor.
 *
 * A workspace with no agents and no executions yet swaps layers 2–3 for the
 * first-run setup checklist — an empty install renders guidance, not a blank
 * health section. The bell stays mounted (it simply shows no badge).
 */
export default function DashboardPage() {
	const queryClient = useQueryClient();
	const hasAgents = useHasAgents();
	const executions = useRecentExecutions();

	// First-run only when BOTH probes have resolved empty — while they load we
	// render the normal layout (its own skeletons) instead of flashing the
	// checklist at every returning user for a frame.
	const isFirstRun = hasAgents.data === false && executions.data?.sampled === 0;

	return (
		<PageShell>
			<PageHeader
				title="Dashboard"
				subtitle="An at-a-glance overview of your jentic-one workspace."
				animated={false}
				actions={
					<>
						<ActionInboxBell />
						<QuickActionsMenu />
						<RefreshButton
							onRefresh={() =>
								queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
							}
							title="Refresh dashboard"
						/>
						<PageHelp
							title="About the Dashboard"
							intro="The landing page layers what needs you now (the bell's action queue), how the gateway is doing (real usage statistics), and what just ran."
							sections={[
								{
									heading: 'Independently sourced',
									body: 'Each widget reads its own endpoint, so one source being unavailable degrades only its rows. The Gateway health section reads the monitoring usage aggregate and is visible to org admins only.',
								},
							]}
							links={[
								{
									href: ROUTES.monitor,
									label: 'Open Monitor for the full activity log',
								},
							]}
						/>
					</>
				}
			/>

			{isFirstRun ? <FirstRunChecklist /> : <GatewayHealthSection />}

			<RecentActivityCard />
		</PageShell>
	);
}
