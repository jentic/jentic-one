import { type JSX } from 'react';
import { ExternalLink, Briefcase, Workflow } from 'lucide-react';
import { JobStatusPill } from './JobStatusPill';
import type { JobLogEntry } from '@/components/monitor/types';
import { formatDuration, formatRelativeTime } from '@/components/monitor/shared/format';
import { Pagination } from '@/components/ui/Pagination';
import { DataTable, type Column } from '@/components/ui/DataTable';

interface JobsTableProps {
	jobs: JobLogEntry[];
	totalCount: number;
	page: number;
	pageSize: number;
	isLoading: boolean;
	onRowClick: (job: JobLogEntry) => void;
	onPageChange: (page: number) => void;
}

function KindCell({ kind }: { kind: JobLogEntry['kind'] }): JSX.Element {
	const isWorkflow = kind === 'workflow';
	const Icon = isWorkflow ? Workflow : Briefcase;
	return (
		<div className="flex items-center gap-1.5">
			<Icon className="text-muted-foreground h-3.5 w-3.5" aria-hidden="true" />
			<span className="text-foreground text-xs capitalize">
				{kind === 'unknown' ? '—' : kind}
			</span>
		</div>
	);
}

export function JobsTable({
	jobs,
	totalCount,
	page,
	pageSize,
	isLoading,
	onRowClick,
	onPageChange,
}: JobsTableProps): JSX.Element {
	const columns: Column<JobLogEntry>[] = [
		{
			key: 'status',
			header: 'Status',
			className: 'w-[110px]',
			render: (row) => <JobStatusPill status={row.status} />,
		},
		{
			key: 'kind',
			header: 'Kind',
			className: 'w-[110px]',
			render: (row) => <KindCell kind={row.kind} />,
		},
		{
			key: 'capability',
			header: 'Capability',
			render: (row) => (
				<span className="text-muted-foreground truncate font-mono text-xs">
					{row.capability ?? '—'}
				</span>
			),
		},
		{
			key: 'toolkitName',
			header: 'Toolkit',
			className: 'w-[140px]',
			render: (row) => (
				<span className="text-muted-foreground text-xs">{row.toolkitName ?? '—'}</span>
			),
		},
		{
			key: 'agentName',
			header: 'Agent',
			className: 'w-[140px]',
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
					data={jobs}
					getRowKey={(row) => row.jobId}
					isLoading={isLoading}
					emptyMessage="No jobs match your filters."
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
