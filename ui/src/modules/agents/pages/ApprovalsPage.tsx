/**
 * Approvals list page — `/app/agents/approvals`, a subsection of Agents.
 *
 * Lists the execution approvals the signed-in reviewer may see (the agent's
 * owner, or an org admin), pending first by default. A row opens the detail.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowLeft, CheckSquare } from 'lucide-react';
import {
	Button,
	DataTable,
	EmptyState,
	PageHeader,
	PageShell,
	Select,
	SkeletonRows,
	type Column,
} from '@/shared/ui';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';
import { useApprovals } from '@/modules/agents/api/approvals-hooks';
import { ApprovalStateBadge } from '@/modules/agents/components/approvals/ApprovalStateBadge';
import { ApprovalsHelp } from '@/modules/agents/components/approvals/ApprovalsHelp';
import { APPROVAL_STATE_OPTIONS } from '@/modules/agents/lib/approvalState';
import type {
	ExecutionApprovalResponse,
	ListApprovalsParams,
} from '@/modules/agents/api/approvals-client';

const COLUMNS: Column<ExecutionApprovalResponse>[] = [
	{
		key: 'state',
		header: 'State',
		render: (row) => <ApprovalStateBadge state={row.state} />,
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
	// Cursors of the pages before the current one (keyset pagination).
	const [cursors, setCursors] = useState<string[]>([]);
	const cursor = cursors.length ? cursors[cursors.length - 1] : null;

	const { data, isLoading, error, refetch } = useApprovals({
		state: (stateFilter || null) as ListApprovalsParams['state'],
		cursor,
	});

	return (
		<PageShell>
			<PageHeader
				title="Approvals"
				subtitle="Agent calls held by a require-approval rule, waiting for a reviewer."
				actions={
					<>
						<Button variant="ghost" size="sm" onClick={() => navigate(ROUTES.agents)}>
							<ArrowLeft className="mr-1 h-4 w-4" />
							Agents
						</Button>
						<Button variant="outline" size="sm" onClick={() => refetch()}>
							Refresh
						</Button>
						<ApprovalsHelp />
					</>
				}
			/>

			<div className="flex items-center gap-3">
				<Select
					value={stateFilter}
					onChange={(e) => {
						setStateFilter(e.target.value);
						setCursors([]);
					}}
					aria-label="Filter by state"
					className="w-40"
				>
					{APPROVAL_STATE_OPTIONS.map((opt) => (
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
					onRowClick={(row) => navigate(ROUTE_PATHS.approval(row.id))}
					getRowLabel={(row) =>
						`View approval for ${row.method} ${row.path} (${row.state})`
					}
				/>
			)}

			{(cursors.length > 0 || data?.has_more) && (
				<div className="flex justify-center gap-3 pt-2">
					<Button
						variant="outline"
						size="sm"
						disabled={cursors.length === 0}
						onClick={() => setCursors((c) => c.slice(0, -1))}
					>
						Previous
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled={!data?.has_more || !data.next_cursor}
						onClick={() => {
							const next = data?.next_cursor;
							if (next) setCursors((c) => [...c, next]);
						}}
					>
						Next
					</Button>
				</div>
			)}
		</PageShell>
	);
}
