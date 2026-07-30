import { Activity, ArrowUpRight } from 'lucide-react';
import {
	Card,
	CardBody,
	DataTable,
	StatusBadge,
	Badge,
	ErrorAlert,
	SkeletonRows,
	AppLink,
	type Column,
} from '@/shared/ui';
import { useRecentExecutions, type ExecutionResponse } from '@/modules/dashboard/api';
import { ROUTES } from '@/shared/app/routes';
import { timeAgo } from '@/shared/lib/utils';
import { SectionHeading } from '@/modules/dashboard/components/CardRow';

/**
 * Columns tuned for scanning, not for schema completeness: the status pill
 * leads (a red 500 should jump out while skimming down the left edge), the
 * operation and its toolkit share one two-line cell (they answer the same
 * "what ran?" question), and duration + recency sit right-aligned at the far
 * edge like every log viewer.
 */
const columns: Column<ExecutionResponse>[] = [
	{
		key: 'status',
		header: 'Status',
		className: 'w-24',
		render: (row) =>
			typeof row.http_status === 'number' ? (
				<StatusBadge status={row.http_status} />
			) : (
				<Badge>{row.status}</Badge>
			),
	},
	{
		key: 'operation_id',
		header: 'Operation',
		render: (row) => (
			<span className="block min-w-0">
				<span className="text-foreground block truncate font-mono text-xs">
					{row.operation_id ?? '—'}
				</span>
				<span className="text-muted-foreground mt-0.5 block truncate text-[11px]">
					{row.toolkit_id || '—'}
				</span>
			</span>
		),
	},
	{
		key: 'duration_ms',
		header: 'Duration',
		className: 'text-muted-foreground w-24 text-right font-mono text-xs tabular-nums',
		render: (row) => (typeof row.duration_ms === 'number' ? `${row.duration_ms}ms` : '—'),
	},
	{
		key: 'created_at',
		header: 'Time',
		className: 'text-muted-foreground w-24 text-right font-mono text-xs',
		render: (row) => timeAgo(row.created_at),
	},
];

/**
 * Latest execution activity — the DETAIL layer of the dashboard, trimmed to a
 * five-row teaser. Composed from `GET /executions` (small sample); the
 * aggregate story lives in the Gateway-health layer above, so this section
 * only answers "what ran just now?" and links into Monitor for the full,
 * filterable execution log.
 *
 * Rendered with the same section grammar as Gateway health (external h2
 * heading + icon medallion, cards below) so the two layers read as siblings.
 */
export function RecentActivityCard() {
	const { data, isLoading, isError, error } = useRecentExecutions();

	return (
		<section aria-label="Recent activity" className="flex flex-col gap-4">
			<SectionHeading
				icon={<Activity className="h-4 w-4" aria-hidden="true" />}
				trailing={
					<AppLink
						href={ROUTES.monitor}
						className="text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline"
					>
						View all
						<ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
					</AppLink>
				}
			>
				Recent activity
			</SectionHeading>
			<Card>
				<CardBody className="px-0 py-0">
					{isLoading ? (
						<div className="px-5 py-4">
							<SkeletonRows rows={5} />
						</div>
					) : isError ? (
						<div className="p-5">
							<ErrorAlert message={error ?? 'Failed to load executions.'} />
						</div>
					) : (
						<DataTable
							columns={columns}
							data={(data?.executions ?? []).slice(0, 5)}
							getRowKey={(row) => row.execution_id}
							ariaLabel="Recent activity"
							emptyMessage="No executions yet. Activity appears here once agents start calling APIs."
						/>
					)}
				</CardBody>
			</Card>
		</section>
	);
}
