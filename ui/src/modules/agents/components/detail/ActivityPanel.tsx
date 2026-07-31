/**
 * ActivityPanel — the detail page's Activity tab: the shared console chart
 * pair (stacked execution volume + total-call trend, `ExecutionVolumeCharts`)
 * plus the shared recent-executions feed (`RecentExecutionsCard`), both
 * scoped by `actor_id`. Monitor owns the full history (cursor paging, trace
 * sheets, filters), so the panel deep-links there rather than
 * re-implementing any of that here.
 *
 * Both sources are permission-gated (usage behind `org:admin`, executions
 * behind `executions:read`): a 403 resolves to `null` (not an error) and the
 * panel renders one quiet permission note instead of charts.
 */
import { Activity } from 'lucide-react';
import { EmptyState, ExecutionVolumeCharts, LoadingState, RecentExecutionsCard } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { useActorExecutions, useActorUsageDetail } from '@/modules/agents/api';

interface ActivityPanelProps {
	actorId: string;
	actorType: 'agent' | 'service_account';
}

export function ActivityPanel({ actorId, actorType }: ActivityPanelProps) {
	const usage = useActorUsageDetail(actorId);
	const executions = useActorExecutions(actorId);

	// Monitor's Executions lens, pre-filtered to this actor — built by the
	// shared route helper so the param vocabulary can't drift across modules.
	const monitorLink = ROUTE_PATHS.monitorExecutions({ actorId, actorType });

	if (usage.isPending || executions.isPending) {
		return <LoadingState size="sm" message="Loading activity…" />;
	}

	// `null` is the client's explicit 403 sentinel; only when BOTH sources are
	// permission-gated does the panel read as "you can't see this". An errored
	// query resolves `data === undefined` and must not masquerade as a
	// permission gate.
	if (usage.data === null && executions.data === null) {
		return (
			<EmptyState
				icon={<Activity className="text-muted-foreground h-6 w-6" />}
				title="Activity requires elevated access"
				description="Execution history and usage statistics require monitoring permissions."
			/>
		);
	}
	if (usage.data == null && executions.data == null) {
		return (
			<EmptyState
				icon={<Activity className="text-muted-foreground h-6 w-6" />}
				title="Couldn't load activity"
				description="Something went wrong while loading this actor's activity. Try again."
			/>
		);
	}

	const buckets = usage.data?.buckets ?? [];
	const items = executions.data?.items ?? [];

	return (
		<div className="space-y-4">
			{usage.data != null && (
				<ExecutionVolumeCharts
					buckets={buckets}
					bucketSeconds={usage.data.bucketSeconds}
					emptyMessage="No executions in the last 7 days. Volume appears here once this actor starts making calls."
				/>
			)}

			{executions.data != null && (
				<RecentExecutionsCard
					monitorHref={monitorLink}
					emptyMessage="No executions recorded for this actor yet."
					hasMore={executions.data.hasMore}
					items={items.map((row) => ({
						id: row.id,
						status: row.status,
						httpStatus: row.httpStatus,
						label: `${row.toolkitName ?? row.toolkitId}${
							row.operationId ? `.${row.operationId}` : ''
						}`,
						error: row.error,
						durationMs: row.durationMs,
						startedAt: row.startedAt,
					}))}
				/>
			)}
		</div>
	);
}
