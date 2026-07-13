import { Activity } from 'lucide-react';
import {
	Card,
	CardHeader,
	CardBody,
	CardTitle,
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
import { CardHeaderIcon } from '@/modules/dashboard/components/CardRow';

const columns: Column<ExecutionResponse>[] = [
	{
		key: 'created_at',
		header: 'Time',
		className: 'text-muted-foreground font-mono text-xs',
		render: (row) => timeAgo(row.created_at),
	},
	{
		key: 'toolkit_id',
		header: 'Toolkit',
		className: 'max-w-[160px] truncate',
		render: (row) => row.toolkit_id || '—',
	},
	{
		key: 'operation_id',
		header: 'Operation',
		className: 'max-w-[220px] truncate font-mono text-xs text-muted-foreground',
		render: (row) => row.operation_id ?? '—',
	},
	{
		key: 'status',
		header: 'Status',
		render: (row) =>
			typeof row.http_status === 'number' ? (
				<StatusBadge status={row.http_status} />
			) : (
				<Badge>{row.status}</Badge>
			),
	},
	{
		key: 'duration_ms',
		header: 'Duration',
		className: 'text-muted-foreground',
		render: (row) => (typeof row.duration_ms === 'number' ? `${row.duration_ms}ms` : '—'),
	},
];

/**
 * Latest execution activity. Composed from `GET /executions` (small sample).
 * Links into Monitor for the full, filterable execution log.
 */
export function RecentActivityCard() {
	const { data, isLoading, isError, error } = useRecentExecutions();

	return (
		<Card>
			<CardHeader className="flex items-center justify-between gap-3">
				<CardTitle as="h2" className="flex items-center gap-2.5">
					<CardHeaderIcon>
						<Activity className="h-4 w-4" aria-hidden="true" />
					</CardHeaderIcon>
					Recent activity
				</CardTitle>
				<AppLink
					href={ROUTES.monitor}
					className="text-primary text-sm font-medium hover:underline"
				>
					View all
				</AppLink>
			</CardHeader>
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
						data={(data?.executions ?? []).slice(0, 10)}
						getRowKey={(row) => row.execution_id}
						ariaLabel="Recent activity"
						emptyMessage="No executions yet. Activity appears here once agents start calling APIs."
					/>
				)}
			</CardBody>
		</Card>
	);
}
