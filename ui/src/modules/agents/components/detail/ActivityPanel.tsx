/**
 * ActivityPanel — the detail page's Activity tab: this actor's execution
 * volume (7-day stacked bars) plus a most-recent-executions feed, both scoped
 * by `actor_id`. Monitor owns the full history (cursor paging, trace sheets,
 * filters), so the panel ends in a pre-filtered "Open in Monitor" deep-link
 * rather than re-implementing any of that here.
 *
 * Both sources are permission-gated (usage behind `org:admin`, executions
 * behind `executions:read`): a 403 resolves to `null` (not an error) and the
 * panel renders one quiet permission note instead of charts.
 */
import { Activity, ArrowRight, ListOrdered, TrendingUp } from 'lucide-react';
import {
	AppLink,
	DataTable,
	DetailSection,
	EmptyState,
	LoadingState,
	StackedBarChart,
	StatusBadge,
	type Column,
	type StackedBarDatum,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useActorExecutions,
	useActorUsageDetail,
	type ActorExecutionEntity,
} from '@/modules/agents/api';

/** "412ms" / "1.2s", or an em-dash for executions without a duration. */
function formatDuration(ms: number | null): string {
	if (ms == null) return '—';
	if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
	return `${Math.round(ms)}ms`;
}

/**
 * Label a bucket timestamp for the x-axis: clock time for sub-day buckets,
 * month + day otherwise (mirrors the dashboard's bucket-label convention).
 */
function bucketLabel(ts: number, bucketSeconds: number): string {
	const date = new Date(ts * 1000);
	if (bucketSeconds < 86_400) {
		return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** The operation cell: "toolkit.operation" with the error inlined for failures. */
function OperationCell({ row }: { row: ActorExecutionEntity }) {
	const toolkit = row.toolkitName ?? row.toolkitId;
	return (
		<div className="min-w-0">
			<code className="text-foreground/90 block truncate font-mono text-xs">
				{toolkit}
				{row.operationId ? `.${row.operationId}` : ''}
			</code>
			{row.error && (
				<span className="text-danger block truncate text-[11px]" title={row.error}>
					{row.error}
				</span>
			)}
		</div>
	);
}

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
	const bars: StackedBarDatum[] = buckets.map((b) => ({
		key: String(b.ts),
		label: bucketLabel(b.ts, usage.data?.bucketSeconds ?? 86_400),
		segments: [
			{
				key: 'success',
				label: 'succeeded',
				value: b.success,
				colorClassName: 'bg-accent-green/80',
			},
			{
				key: 'failed',
				label: 'failed',
				value: b.failed,
				colorClassName: 'bg-danger/70',
			},
		],
	}));

	const items = executions.data?.items ?? [];
	const columns: Column<ActorExecutionEntity>[] = [
		{
			key: 'status',
			header: 'Status',
			className: 'w-20',
			render: (row) =>
				row.httpStatus != null ? (
					<StatusBadge status={row.httpStatus} />
				) : (
					<span className="text-muted-foreground text-xs">{row.status}</span>
				),
		},
		{
			key: 'operation',
			header: 'Operation',
			className: 'max-w-[360px]',
			render: (row) => <OperationCell row={row} />,
		},
		{
			key: 'duration',
			header: 'Duration',
			className: 'w-24 text-right',
			render: (row) => (
				<span className="text-muted-foreground font-mono text-xs tabular-nums">
					{formatDuration(row.durationMs)}
				</span>
			),
		},
		{
			key: 'time',
			header: 'Time',
			className: 'w-28 text-right',
			render: (row) => (
				<span
					className="text-muted-foreground text-xs"
					title={formatTimestamp(row.startedAt)}
				>
					{timeAgo(row.startedAt)}
				</span>
			),
		},
	];

	return (
		<div className="space-y-4">
			{usage.data != null && (
				<DetailSection
					title="Execution volume · 7d"
					icon={<TrendingUp className="h-4 w-4" />}
				>
					{buckets.length === 0 ? (
						<p className="text-muted-foreground py-6 text-center text-sm">
							No executions in the last 7 days.
						</p>
					) : (
						<StackedBarChart
							bars={bars}
							height={120}
							ariaLabel={`Execution volume over the last 7 days: ${usage.data.total} total, ${usage.data.failed} failed.`}
						/>
					)}
				</DetailSection>
			)}

			{executions.data != null && (
				<DetailSection
					title="Recent executions"
					icon={<ListOrdered className="h-4 w-4" />}
					trailing={
						<AppLink
							href={monitorLink}
							className="text-muted-foreground hover:text-primary inline-flex items-center gap-1 text-xs font-medium transition-colors"
						>
							Open in Monitor <ArrowRight className="h-3.5 w-3.5" />
						</AppLink>
					}
					bodyClassName="px-0 py-0 space-y-0"
				>
					<DataTable<ActorExecutionEntity>
						columns={columns}
						data={items}
						getRowKey={(row) => row.id}
						emptyMessage="No executions recorded for this actor yet."
						ariaLabel="Recent executions"
						renderCard={(row) => (
							<div className="space-y-1.5">
								<div className="flex items-center justify-between gap-2">
									{row.httpStatus != null ? (
										<StatusBadge status={row.httpStatus} />
									) : (
										<span className="text-muted-foreground text-xs">
											{row.status}
										</span>
									)}
									<span
										className="text-muted-foreground text-[11px]"
										title={formatTimestamp(row.startedAt)}
									>
										{timeAgo(row.startedAt)}
									</span>
								</div>
								<OperationCell row={row} />
							</div>
						)}
					/>
					{executions.data.hasMore && (
						<p className="text-muted-foreground border-border/60 border-t px-4 py-2.5 text-xs">
							Showing the {items.length} most recent —{' '}
							<AppLink href={monitorLink} className="text-primary font-medium">
								see the full history in Monitor
							</AppLink>
							.
						</p>
					)}
				</DetailSection>
			)}
		</div>
	);
}
