import { Activity, Lock } from 'lucide-react';
import {
	ActorLabel,
	DetailSection,
	EmptyRow,
	ExecutionVolumeCharts,
	RecentExecutionsCard,
} from '@/shared/ui';
import { useToolkitExecutions, useToolkitUsage } from '@/modules/toolkits/api';
import { ROUTE_PATHS } from '@/shared/app/routes';

/**
 * Activity tab — what this toolkit has actually been doing: the shared
 * console chart pair (stacked execution volume + total-call trend,
 * `ExecutionVolumeCharts`, fed by `GET /monitoring/usage?toolkit_id=…`) and
 * the shared recent-executions feed (`RecentExecutionsCard`, fed by
 * `GET /executions?toolkit_id=…`), with a deep-link into Monitor's full
 * filterable log carrying the toolkit filter.
 *
 * Both endpoints are admin-gated; the repository maps 401/403 to `null` and
 * this tab degrades to a single explanatory panel for non-admins.
 */
export function ActivityTab({ toolkitId }: { toolkitId: string }) {
	const { data: usage, isLoading: usageLoading } = useToolkitUsage(toolkitId);
	const { data: executions, isLoading: executionsLoading } = useToolkitExecutions(toolkitId);

	const loading = usageLoading || executionsLoading;
	const adminBlocked = !loading && usage === null && executions === null;

	if (adminBlocked) {
		return (
			<DetailSection title="Activity" icon={<Activity className="h-4 w-4" />}>
				<EmptyRow icon={<Lock />}>
					Execution history is admin-only. Ask an org admin, or check the toolkit's
					entries in Monitor if you have access.
				</EmptyRow>
			</DetailSection>
		);
	}

	const buckets = usage?.buckets ?? [];
	const rows = executions ?? [];

	return (
		<div className="space-y-6">
			<ExecutionVolumeCharts
				buckets={buckets}
				bucketSeconds={usage?.bucket_seconds ?? 86_400}
				isLoading={loading}
				emptyMessage="No executions in the last 7 days. Volume appears here once agents start calling this toolkit."
			/>

			<RecentExecutionsCard
				isLoading={loading}
				monitorHref={ROUTE_PATHS.monitorExecutions({ toolkitId })}
				emptyMessage="No recent executions for this toolkit."
				items={rows.map((row) => ({
					id: row.execution_id,
					status: row.status,
					httpStatus: row.http_status,
					label: row.operation_id ?? row.api_label ?? row.trace_id,
					error: row.error,
					// Resolve the raw actor id like every other attribution surface.
					meta: <ActorLabel actorId={row.actor_id} actorType={row.actor_type} />,
					durationMs: row.duration_ms,
					startedAt: row.started_at,
				}))}
			/>
		</div>
	);
}
