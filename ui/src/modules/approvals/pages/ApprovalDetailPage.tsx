/**
 * Approval detail page — `/app/approvals/:id`.
 *
 * Shows full context for a single execution approval and presents an
 * approve / deny form for pending ones. Decided approvals are shown
 * read-only.
 */
import { useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { ArrowLeft, CheckSquare } from 'lucide-react';
import {
	Badge,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	EmptyState,
	Label,
	PageHeader,
	PageShell,
	SkeletonRows,
	Textarea,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { ApprovalDecision, useApproval, useDecideApproval } from '@/modules/approvals/api/hooks';

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

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
	return (
		<div className="flex flex-col gap-0.5">
			<span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
				{label}
			</span>
			<span className="text-sm">{value}</span>
		</div>
	);
}

export default function ApprovalDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const [reason, setReason] = useState('');

	const { data: approval, isLoading, error } = useApproval(id);
	const decide = useDecideApproval();

	const isPending = approval?.state === 'pending';

	function handleDecide(decision: ApprovalDecision) {
		if (!id) return;
		decide.mutate(
			{ approvalId: id, body: { decision, reason: reason || null } },
			{
				onSuccess: () => {
					// Stay on the page to show the updated state.
					setReason('');
				},
			},
		);
	}

	return (
		<PageShell width="reading">
			<PageHeader
				title="Execution Approval"
				subtitle="Review the held execution and approve or deny it."
				icon={<CheckSquare className="h-6 w-6" />}
				actions={
					<Button variant="ghost" size="sm" onClick={() => navigate(ROUTES.approvals)}>
						<ArrowLeft className="mr-1 h-4 w-4" />
						All approvals
					</Button>
				}
			/>

			{isLoading ? (
				<SkeletonRows rows={8} />
			) : error ? (
				<EmptyState
					icon={<CheckSquare className="h-8 w-8" />}
					title="Failed to load approval"
					description={error.message}
				/>
			) : !approval ? (
				<EmptyState icon={<CheckSquare className="h-8 w-8" />} title="Approval not found" />
			) : (
				<div className="flex flex-col gap-6">
					{/* Summary */}
					<Card>
						<CardHeader>
							<CardTitle className="flex items-center gap-2">
								<Badge variant={stateVariant(approval.state)}>
									{approval.state}
								</Badge>
								<span>
									{approval.method} {approval.path}
								</span>
							</CardTitle>
						</CardHeader>
						<CardBody className="grid gap-4 sm:grid-cols-2">
							<DetailRow
								label="API"
								value={`${approval.api_vendor}/${approval.api_name} ${approval.api_version}`}
							/>
							<DetailRow label="Operation" value={approval.operation_id ?? '—'} />
							<DetailRow
								label="Agent"
								value={
									<span className="font-mono text-xs">{approval.agent_id}</span>
								}
							/>
							<DetailRow
								label="Credential"
								value={
									<span className="font-mono text-xs">
										{approval.credential_id}
									</span>
								}
							/>
							<DetailRow
								label="Requested"
								value={new Date(approval.created_at).toLocaleString()}
							/>
							<DetailRow
								label="Expires"
								value={new Date(approval.expires_at).toLocaleString()}
							/>
							{approval.trace_id && (
								<DetailRow
									label="Trace ID"
									value={
										<span className="font-mono text-xs">
											{approval.trace_id}
										</span>
									}
								/>
							)}
							{approval.matched_rule_id && (
								<DetailRow
									label="Matched rule"
									value={
										<span className="font-mono text-xs">
											{approval.matched_rule_id}
										</span>
									}
								/>
							)}
						</CardBody>
					</Card>

					{/* Decision (pending only) */}
					{isPending && (
						<Card>
							<CardHeader>
								<CardTitle>Decision</CardTitle>
							</CardHeader>
							<CardBody className="flex flex-col gap-4">
								<div>
									<Label htmlFor="reason">Reason (optional)</Label>
									<Textarea
										id="reason"
										placeholder="Add a note for the audit trail…"
										value={reason}
										onChange={(e) => setReason(e.target.value)}
										rows={3}
										className="mt-1"
									/>
								</div>
								<div className="flex gap-3">
									<Button
										variant="primary"
										onClick={() => handleDecide(ApprovalDecision.APPROVE)}
										disabled={decide.isPending}
									>
										Approve
									</Button>
									<Button
										variant="danger"
										onClick={() => handleDecide(ApprovalDecision.DENY)}
										disabled={decide.isPending}
									>
										Deny
									</Button>
								</div>
							</CardBody>
						</Card>
					)}

					{/* Outcome (already decided) */}
					{!isPending && approval.decided_by && (
						<Card>
							<CardHeader>
								<CardTitle>Decision record</CardTitle>
							</CardHeader>
							<CardBody className="grid gap-4 sm:grid-cols-2">
								<DetailRow label="Decided by" value={approval.decided_by} />
								<DetailRow
									label="Decided at"
									value={
										approval.decided_at
											? new Date(approval.decided_at).toLocaleString()
											: '—'
									}
								/>
								{approval.decision_reason && (
									<DetailRow label="Reason" value={approval.decision_reason} />
								)}
							</CardBody>
						</Card>
					)}
				</div>
			)}
		</PageShell>
	);
}
