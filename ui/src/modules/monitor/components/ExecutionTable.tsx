/**
 * ExecutionTable — the columned execution log, ported from jentic-mini's
 * `execution-log/ExecutionTable.tsx`.
 *
 * Renders `GET /executions` rows in the same table vocabulary mini used:
 * Status | API (vendor chip) | Operation | Toolkit | Agent | Duration | When,
 * with a trailing open-affordance glyph. Built on the shared `<DataTable>`
 * (keyboard-activatable rows, scroll-region a11y) instead of mini's simpler
 * grid, and on jentic-one's `<VendorIcon>` (deterministic gradient+initials —
 * mini's brand-logo vendor registry is mini-specific infra).
 *
 * Deliberate adaptations from mini:
 * - No `JobBadge` column chip: jentic-one execution records carry no `job_id`
 *   (the Jobs lens links the other way, job → execution).
 * - Cursor paging lives in the parent (`CursorPager`); mini's page-numbered
 *   `<Pagination>` doesn't fit the backend's cursor contract.
 * - On phones the table renders as stacked cards (shared DataTable feature)
 *   rather than horizontally scrolling.
 */
import { ExternalLink } from 'lucide-react';
import { ActorLabel, DataTable, VendorIcon, type Column } from '@/shared/ui';
import { toExecutionStatus } from '@/modules/monitor/api';
import type { ExecutionResponse } from '@/modules/monitor/api';
import { ExecutionStatusPill } from '@/modules/monitor/components/StatusPill';
import { formatDuration, formatRelative } from '@/modules/monitor/lib/format';

interface ExecutionTableProps {
	executions: ExecutionResponse[];
	isLoading: boolean;
	onRowClick: (execution: ExecutionResponse) => void;
}

function VendorChip({ vendor, apiName }: { vendor: string; apiName: string }) {
	return (
		<div className="flex items-center gap-2">
			<VendorIcon
				name={apiName}
				vendor={vendor}
				size="sm"
				className="h-6 w-6 rounded-md text-[8px]"
			/>
			<span className="text-foreground truncate text-sm">{apiName}</span>
		</div>
	);
}

function apiDisplayName(row: ExecutionResponse): string {
	return row.api?.name ?? row.api?.host ?? 'unknown';
}

export function ExecutionTable({ executions, isLoading, onRowClick }: ExecutionTableProps) {
	const columns: Column<ExecutionResponse>[] = [
		{
			key: 'status',
			header: 'Status',
			className: 'w-[110px]',
			render: (row) => <ExecutionStatusPill status={toExecutionStatus(row.status)} />,
		},
		{
			key: 'api',
			header: 'API',
			render: (row) => (
				<VendorChip vendor={row.api?.vendor ?? 'unknown'} apiName={apiDisplayName(row)} />
			),
		},
		{
			key: 'operation_id',
			header: 'Operation',
			render: (row) => (
				<div className="flex min-w-0 flex-col">
					<span className="text-muted-foreground truncate font-mono text-xs">
						{row.operation_id ?? '—'}
					</span>
					{row.error && (
						<span className="text-danger mt-0.5 truncate text-xs" title={row.error}>
							{row.error}
						</span>
					)}
				</div>
			),
		},
		{
			key: 'toolkit_id',
			header: 'Toolkit',
			className: 'w-[160px]',
			render: (row) => (
				<div className="flex flex-col leading-tight">
					<span className="text-foreground text-xs">
						{row.toolkit_name ?? row.toolkit_id}
					</span>
					{row.origin && (
						<span className="text-muted-foreground text-[10px] capitalize">
							{row.origin}
						</span>
					)}
				</div>
			),
		},
		{
			key: 'actor_id',
			header: 'Agent',
			className: 'w-[160px]',
			render: (row) =>
				row.actor_id ? (
					<ActorLabel
						actorId={row.actor_id}
						actorType={row.actor_type}
						className="text-muted-foreground text-xs"
					/>
				) : (
					<span className="text-muted-foreground font-mono text-xs">
						{row.actor_type}
					</span>
				),
		},
		{
			key: 'duration_ms',
			header: 'Duration',
			className: 'w-[100px] text-right',
			render: (row) => (
				<span className="text-foreground font-mono text-xs">
					{formatDuration(row.duration_ms)}
				</span>
			),
		},
		{
			key: 'started_at',
			header: 'When',
			className: 'w-[110px] text-right',
			render: (row) => (
				<span className="text-muted-foreground text-xs">
					{formatRelative(row.started_at)}
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

	return (
		<div className="border-border bg-card rounded-xl border">
			<DataTable
				columns={columns}
				data={executions}
				getRowKey={(row) => row.execution_id}
				isLoading={isLoading}
				emptyMessage="No executions match your filters."
				onRowClick={onRowClick}
				ariaLabel="Executions"
				getRowLabel={(row) => `View trace for ${row.operation_id ?? row.execution_id}`}
				renderCard={(row) => (
					<div className="space-y-2">
						<div className="flex items-center justify-between gap-2">
							<ExecutionStatusPill status={toExecutionStatus(row.status)} />
							<span className="text-muted-foreground text-xs">
								{formatRelative(row.started_at)}
							</span>
						</div>
						<div className="text-foreground truncate font-mono text-sm">
							{row.operation_id ?? '—'}
						</div>
						{row.error && (
							<div className="text-danger truncate text-xs" title={row.error}>
								{row.error}
							</div>
						)}
						<div className="text-muted-foreground flex items-center justify-between gap-2 text-xs">
							<span className="truncate">{apiDisplayName(row)}</span>
							{row.actor_id ? (
								<ActorLabel
									actorId={row.actor_id}
									actorType={row.actor_type}
									className="truncate"
								/>
							) : (
								<span className="truncate font-mono">{row.actor_type}</span>
							)}
						</div>
						<div className="text-muted-foreground flex items-center justify-between gap-2 text-xs">
							<span className="truncate">{row.toolkit_name ?? row.toolkit_id}</span>
							<span className="font-mono">{formatDuration(row.duration_ms)}</span>
						</div>
					</div>
				)}
			/>
		</div>
	);
}
