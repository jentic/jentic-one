import { PageShell, PageHeader, PageHelp, RefreshButton } from '@/shared/ui';
import { useQueryClient } from '@tanstack/react-query';
import { dashboardKeys } from '@/modules/dashboard/api';
import { OverviewCards } from '@/modules/dashboard/components/OverviewCards';
import { PendingAgentsCard } from '@/modules/dashboard/components/PendingAgentsCard';
import { PendingAccessRequestsCard } from '@/modules/dashboard/components/PendingAccessRequestsCard';
import { AlertsCard } from '@/modules/dashboard/components/AlertsCard';
import { RecentActivityCard } from '@/modules/dashboard/components/RecentActivityCard';
import { QuickActions } from '@/modules/dashboard/components/QuickActions';
import { ROUTES } from '@/shared/app/routes';

/**
 * Dashboard — the `/app` index (landing) page.
 *
 * There is no aggregate/stats endpoint: the overview is composed CLIENT-SIDE
 * from four existing list endpoints (agents, events, executions, apis), each
 * read through the shared api facade (never by importing sibling modules) and
 * each owning its own loading/error state so one failing source degrades only
 * its widget. See COLLABORATION.md §1.5 + the Dashboard brief.
 */
export default function DashboardPage() {
	const queryClient = useQueryClient();

	return (
		<PageShell>
			<PageHeader
				title="Dashboard"
				subtitle="An at-a-glance overview of your jentic-one workspace."
				animated={false}
				actions={
					<>
						<RefreshButton
							onRefresh={() =>
								queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
							}
							title="Refresh dashboard"
						/>
						<PageHelp
							title="About the Dashboard"
							intro="The landing page composes a live overview from your agents, events, executions, and registered APIs."
							sections={[
								{
									heading: 'Composed, not aggregated',
									body: 'There is no stats endpoint — each widget reads a small page from an existing list endpoint, so one source being unavailable degrades only its card.',
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

			<OverviewCards />

			<div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
				<PendingAgentsCard />
				<PendingAccessRequestsCard />
			</div>

			<AlertsCard />

			<RecentActivityCard />

			<QuickActions />
		</PageShell>
	);
}
