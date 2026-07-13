import { KeyRound, Bell, Activity, Boxes } from 'lucide-react';
import {
	usePendingAgents,
	useActionableEvents,
	useRecentExecutions,
	useCatalogSize,
	formatApproxCount,
} from '@/modules/dashboard/api';
import { StatCard } from '@/modules/dashboard/components/StatCard';
import { formatPercent } from '@/modules/dashboard/components/format';
import { ROUTES } from '@/shared/app/routes';

/**
 * The four headline tiles, each composed from its own list endpoint. Every
 * tile reads its own query's state so a single source failing (partial error)
 * degrades only that tile — the rest of the overview still renders. Each tile
 * links into the surface that owns its number, so the count is a jump-off.
 */
export function OverviewCards() {
	const agents = usePendingAgents();
	const alerts = useActionableEvents();
	const executions = useRecentExecutions();
	const catalog = useCatalogSize();

	return (
		<div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
			<StatCard
				label="Awaiting approval"
				icon={<KeyRound className="h-4 w-4 shrink-0" aria-hidden="true" />}
				href={ROUTES.agents}
				isLoading={agents.isLoading}
				error={agents.isError ? 'Agents unavailable' : null}
				value={agents.data ? formatApproxCount(agents.data.count) : '—'}
				caption="agents"
			/>
			<StatCard
				label="Needs attention"
				icon={<Bell className="h-4 w-4 shrink-0" aria-hidden="true" />}
				href={`${ROUTES.monitor}?tab=events`}
				isLoading={alerts.isLoading}
				error={alerts.isError ? 'Alerts unavailable' : null}
				value={alerts.data ? formatApproxCount(alerts.data.count) : '—'}
				caption="actionable alerts"
			/>
			<StatCard
				label="Success rate"
				icon={<Activity className="h-4 w-4 shrink-0" aria-hidden="true" />}
				href={ROUTES.monitor}
				isLoading={executions.isLoading}
				error={executions.isError ? 'Executions unavailable' : null}
				value={executions.data ? formatPercent(executions.data.successRate) : '—'}
				caption={
					executions.data && executions.data.sampled > 0
						? `of ${executions.data.sampled} recent`
						: 'no recent activity'
				}
			/>
			<StatCard
				label="APIs registered"
				icon={<Boxes className="h-4 w-4 shrink-0" aria-hidden="true" />}
				href={ROUTES.workspace}
				isLoading={catalog.isLoading}
				error={catalog.isError ? 'Catalog unavailable' : null}
				value={catalog.data ? formatApproxCount(catalog.data.apiCount) : '—'}
				caption="in your workspace"
			/>
		</div>
	);
}
