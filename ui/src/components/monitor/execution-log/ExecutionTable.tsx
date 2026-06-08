import { type JSX } from 'react';
import { ExternalLink } from 'lucide-react';
import { JobBadge } from './JobBadge';
import type { ExecutionLogEntry } from '@/components/monitor/types';
import { StatusPill } from '@/components/monitor/shared/StatusPill';
import { formatDuration, formatRelativeTime } from '@/components/monitor/shared/format';
import { getVendorConfig, getInitials } from '@/components/monitor/shared/vendor-icons';
import { Pagination } from '@/components/ui/Pagination';
import { DataTable, type Column } from '@/components/ui/DataTable';

interface ExecutionTableProps {
	executions: ExecutionLogEntry[];
	totalCount: number;
	page: number;
	pageSize: number;
	isLoading: boolean;
	onRowClick: (execution: ExecutionLogEntry) => void;
	onPageChange: (page: number) => void;
	/** Optional: when set, JobBadge becomes a cross-link to the Jobs tab. */
	onOpenJob?: (jobId: string) => void;
}

function VendorChip({ vendor, apiName }: { vendor: string; apiName: string }) {
	const config = getVendorConfig(vendor);
	return (
		<div className="flex items-center gap-2">
			<div
				className="flex h-6 w-6 flex-shrink-0 items-center justify-center overflow-hidden rounded-md"
				style={{ backgroundColor: config.bg }}
			>
				{config.iconUrl ? (
					<img
						src={config.iconUrl}
						alt={apiName}
						className="h-3.5 w-3.5 object-contain"
						style={{ filter: config.text === '#fff' ? 'invert(1)' : undefined }}
					/>
				) : (
					<span className="text-[8px] font-bold" style={{ color: config.text }}>
						{getInitials(apiName)}
					</span>
				)}
			</div>
			<span className="text-foreground truncate text-sm">{apiName}</span>
		</div>
	);
}

export function ExecutionTable({
	executions,
	totalCount,
	page,
	pageSize,
	isLoading,
	onRowClick,
	onPageChange,
	onOpenJob,
}: ExecutionTableProps): JSX.Element {
	const columns: Column<ExecutionLogEntry>[] = [
		{
			key: 'status',
			header: 'Status',
			className: 'w-[110px]',
			render: (row) => <StatusPill status={row.status} />,
		},
		{
			key: 'apiName',
			header: 'API',
			render: (row) => (
				<VendorChip
					vendor={row.apiVendor ?? 'unknown'}
					apiName={row.apiName ?? 'unknown'}
				/>
			),
		},
		{
			key: 'operationName',
			header: 'Operation',
			render: (row) => (
				<div className="flex items-center gap-2">
					<span className="text-muted-foreground truncate font-mono text-xs">
						{row.operationName ?? row.workflowName ?? '—'}
					</span>
					{row.jobId ? <JobBadge jobId={row.jobId} onOpen={onOpenJob} /> : null}
				</div>
			),
		},
		{
			key: 'toolkitName',
			header: 'Toolkit',
			className: 'w-[160px]',
			render: (row) => (
				<div className="flex flex-col leading-tight">
					<span className="text-foreground text-xs">{row.toolkitName ?? '—'}</span>
					<span className="text-muted-foreground text-[10px] capitalize">
						{row.executionType}
					</span>
				</div>
			),
		},
		{
			key: 'agentName',
			header: 'Agent',
			className: 'w-[160px]',
			render: (row) => (
				<span className="text-muted-foreground text-xs">{row.agentName ?? '—'}</span>
			),
		},
		{
			key: 'durationMs',
			header: 'Duration',
			className: 'w-[100px] text-right',
			render: (row) => (
				<span className="text-foreground font-mono text-xs">
					{formatDuration(row.durationMs)}
				</span>
			),
		},
		{
			key: 'createdAt',
			header: 'When',
			className: 'w-[110px] text-right',
			render: (row) => (
				<span className="text-muted-foreground text-xs">
					{formatRelativeTime(row.createdAt)}
				</span>
			),
		},
		{
			key: '_open',
			header: '',
			className: 'w-[40px]',
			render: () => (
				<ExternalLink className="text-muted-foreground h-3.5 w-3.5" aria-hidden="true" />
			),
		},
	];

	const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

	return (
		<div className="space-y-3">
			<div className="border-border bg-card rounded-xl border">
				<DataTable
					columns={columns}
					data={executions}
					getRowKey={(row) => row.executionId}
					isLoading={isLoading}
					emptyMessage="No executions match your filters."
					onRowClick={onRowClick}
				/>
			</div>

			{totalCount > pageSize && (
				<Pagination
					page={page}
					totalPages={totalPages}
					totalCount={totalCount}
					pageSize={pageSize}
					onPageChange={onPageChange}
					className="border-border bg-card rounded-xl border"
				/>
			)}
		</div>
	);
}
