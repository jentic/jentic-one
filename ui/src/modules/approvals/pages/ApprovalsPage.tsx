/**
 * Approvals list page — `/app/approvals`.
 *
 * Shows pending (and optionally historical) execution approvals that the
 * current user can review. Clicking a row navigates to the detail page.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { CheckSquare } from 'lucide-react';
import {
	Badge,
	Button,
	DataTable,
	EmptyState,
	PageHeader,
	PageShell,
	Select,
	SkeletonRows,
	type Column,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { useApprovals } from '@/modules/approvals/api/hooks';
import type { ExecutionApprovalResponse } from '@/modules/approvals/api/client';

const STATE_OPTIONS = [
	{ value: '', label: 'All states' },
	{ value: 'pending', label: 'Pending' },
	{ value: 'approved', label: 'Approved' },
	{ value: 'denied', label: 'Denied' },
	{ value: 'expired', label: 'Expired' },
	{ value: 'withdrawn', label: 'Withdrawn' },
];

function stateVariant(state: string): 'warning' | 'success' | 'danger' | 'default' {
	switch (state) {
		case 'pending':
			return 'warning';
		case 'approved':
			return 'success';
		case 'denied':
		case 'expired':
			return 'danger';
		default:
			return 'default';
	}
}

const COLUMNS: Column<ExecutionApprovalResponse>[] = [
	{
		key: 'state',
		header: 'State',
		render: (row) => <Badge variant={stateVariant(row.state)}>{row.state}</Badge>,
	},
	{
		key: 'api_vendor',
		header: 'API',
		render: (row) => (
			<span className="font-mono text-sm">
				{row.api_vendor}/{row.api_name}
			</span>
		),
	},
	{
		key: 'method',
		header: 'Operation',
		render: (row) => (
			<span className="font-mono text-sm">
				{row.method} {row.path}
			</span>
		),
	},
	{
		key: 'agent_id',
		header: 'Agent',
		render: (row) => (
			<span className="text-muted-foreground font-mono text-xs">{row.agent_id}</span>
		),
	},
	{
		key: 'created_at',
		header: 'Requested',
		render: (row) => (
			<span className="text-muted-foreground text-sm">
				{new Date(row.created_at).toLocaleString()}
			</span>
		),
	},
	{
		key: 'expires_at',
		header: 'Expires',
		render: (row) => (
			<span className="text-muted-foreground text-sm">
				{new Date(row.expires_at).toLocaleString()}
			</span>
		),
	},
];

export default function ApprovalsPage() {
	const navigate = useNavigate();
	const [stateFilter, setStateFilter] = useState<string>('pending');

	const { data, isLoading, error, refetch } = useApprovals({
		state: stateFilter || null,
	});

	return (
		<PageShell>
			<PageHeader
				title="Execution Approvals"
				subtitle="Review and decide on held executions awaiting human approval."
				icon={<CheckSquare className="h-6 w-6" />}
				actions={
					<Button variant="outline" size="sm" onClick={() => refetch()}>
						Refresh
					</Button>
				}
			/>

			<div className="flex items-center gap-3">
				<Select
					value={stateFilter}
					onChange={(e) => setStateFilter(e.target.value)}
					aria-label="Filter by state"
					className="w-40"
				>
					{STATE_OPTIONS.map((opt) => (
						<option key={opt.value} value={opt.value}>
							{opt.label}
						</option>
					))}
				</Select>
			</div>

			{isLoading ? (
				<SkeletonRows rows={6} />
			) : error ? (
				<EmptyState
					icon={<CheckSquare className="h-8 w-8" />}
					title="Failed to load approvals"
					description={error.message}
				/>
			) : (
				<DataTable
					columns={COLUMNS}
					data={data?.data ?? []}
					getRowKey={(row) => row.id}
					emptyMessage={
						stateFilter === 'pending'
							? 'No pending approvals.'
							: 'No approvals match this filter.'
					}
					onRowClick={(row) =>
						navigate(`${ROUTES.approvals}/${encodeURIComponent(row.id)}`)
					}
					getRowLabel={(row) =>
						`View approval for ${row.method} ${row.path} (${row.state})`
					}
				/>
			)}

			{data?.has_more && (
				<div className="flex justify-center pt-2">
					<span className="text-muted-foreground text-sm">
						More results available — use the cursor API to paginate.
					</span>
				</div>
			)}
		</PageShell>
	);
}
