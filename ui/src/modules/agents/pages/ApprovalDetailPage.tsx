/**
 * Approval detail page — `/app/agents/approvals/:id`, a subsection of Agents.
 *
 * The review page an agent's held call links to (`GET /executions/holds/{id}`).
 * Shows who filed it (agent and owner), the matched rule, the exact request
 * that runs if approved (method, URL, body), and where the call stands (held,
 * approved, running, its outcome) with the execution record once it has run.
 * A pending, unexpired held execution offers Approve and Deny to anyone who
 * can see it; the server decides who may act (the agent's owner or an org
 * admin) and a refused decision (403) is explained in place. A decided one is
 * read-only. A signed-in user who cannot see it (404) is told who can.
 */
import { useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { ArrowLeft, CheckSquare } from 'lucide-react';
import {
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
import {
	ApprovalDecision,
	ApprovalsApiError,
	HELD_EXECUTION_ERRORS,
	useApproval,
	useDecideApproval,
} from '@/modules/agents/api/approvals-hooks';
import { ApprovalStateBadge } from '@/modules/agents/components/approvals/ApprovalStateBadge';
import { ApprovalsHelp } from '@/modules/agents/components/approvals/ApprovalsHelp';
import { CallStatusCard } from '@/modules/agents/components/approvals/CallStatusCard';
import { isDecidable } from '@/modules/agents/lib/approvalState';

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
	return (
		<div className="flex flex-col gap-0.5">
			<dt className="text-muted-foreground text-xs tracking-wider uppercase">{label}</dt>
			<dd className="text-sm break-all">{value}</dd>
		</div>
	);
}

function Mono({ children }: { children: React.ReactNode }) {
	return <span className="font-mono text-xs">{children}</span>;
}

/** Pretty-print a JSON body; anything else is shown verbatim. */
function formatBody(body: string): string {
	try {
		return JSON.stringify(JSON.parse(body), null, 2);
	} catch {
		return body;
	}
}

export default function ApprovalDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const [reason, setReason] = useState('');

	const { data: approval, isLoading, error } = useApproval(id);
	const decide = useDecideApproval();

	const decidable = approval ? isDecidable(approval.state, approval.expires_at) : false;
	const notReviewer = error instanceof ApprovalsApiError && error.status === 404;
	// The server decides who may act; a viewer it refuses (403) sees why in
	// place of the verbs.
	const decideForbidden =
		decide.error instanceof ApprovalsApiError &&
		(decide.error.status === 403 || decide.error.is(HELD_EXECUTION_ERRORS.decideForbidden));

	function handleDecide(decision: ApprovalDecision) {
		if (!id) return;
		decide.mutate(
			{ heldExecutionId: id, body: { decision, reason: reason || null } },
			{ onSuccess: () => setReason('') },
		);
	}

	return (
		<PageShell width="reading">
			<PageHeader
				title="Review held call"
				subtitle="An agent's call is waiting on a human decision."
				actions={
					<>
						<Button
							variant="ghost"
							size="sm"
							onClick={() => navigate(ROUTES.approvals)}
						>
							<ArrowLeft className="mr-1 h-4 w-4" />
							All approvals
						</Button>
						<ApprovalsHelp />
					</>
				}
			/>

			{isLoading ? (
				<SkeletonRows rows={8} />
			) : notReviewer ? (
				<EmptyState
					icon={<CheckSquare className="h-8 w-8" />}
					title="You can't review this approval"
					description="Only the agent's owner or an org admin can see and decide it. Ask them to open this link."
				/>
			) : error ? (
				<EmptyState
					icon={<CheckSquare className="h-8 w-8" />}
					title="Failed to load approval"
					description={error.message}
				/>
			) : !approval ? (
				<EmptyState icon={<CheckSquare className="h-8 w-8" />} title="Approval not found" />
			) : (
				<>
					<Card>
						<CardHeader>
							<CardTitle className="flex flex-wrap items-center gap-2">
								<ApprovalStateBadge
									state={
										approval.state === 'pending' && !decidable
											? 'expired'
											: approval.state
									}
								/>
								<span className="font-mono">
									{approval.method} {approval.path}
								</span>
							</CardTitle>
						</CardHeader>
						<CardBody>
							<dl className="grid gap-4 sm:grid-cols-2">
								<DetailRow
									label="Agent"
									value={
										<>
											{approval.agent_name ?? 'Unknown agent'}{' '}
											<Mono>({approval.agent_id})</Mono>
										</>
									}
								/>
								<DetailRow
									label="Owner"
									value={
										approval.agent_owner_id ? (
											<Mono>{approval.agent_owner_id}</Mono>
										) : (
											'No owner — org admins review'
										)
									}
								/>
								<DetailRow
									label="API"
									value={`${approval.api_vendor}/${approval.api_name} ${approval.api_version}`}
								/>
								<DetailRow label="Operation" value={approval.operation_id ?? '—'} />
								<DetailRow
									label="Matched rule"
									value={
										approval.matched_rule_id ? (
											<Mono>{approval.matched_rule_id}</Mono>
										) : (
											'—'
										)
									}
								/>
								<DetailRow
									label="Credential"
									value={<Mono>{approval.credential_id}</Mono>}
								/>
								<DetailRow
									label="Requested"
									value={new Date(approval.created_at).toLocaleString()}
								/>
								<DetailRow
									label="Expires"
									value={new Date(approval.expires_at).toLocaleString()}
								/>
							</dl>
						</CardBody>
					</Card>

					<Card>
						<CardHeader>
							<CardTitle>Request</CardTitle>
						</CardHeader>
						<CardBody className="flex flex-col gap-3">
							{approval.request ? (
								<>
									<p className="font-mono text-sm break-all">
										{approval.request.method} {approval.request.url}
									</p>
									{approval.request.body ? (
										<pre
											aria-label="Request body"
											className="bg-muted max-h-96 overflow-auto rounded p-3 font-mono text-xs"
										>
											{formatBody(approval.request.body)}
										</pre>
									) : (
										<p className="text-muted-foreground text-sm">
											No request body.
										</p>
									)}
									{approval.request.body_truncated && (
										<p className="text-muted-foreground text-xs">
											The body is shortened for display; the full body runs if
											approved.
										</p>
									)}
								</>
							) : (
								<p className="text-muted-foreground text-sm">
									The held request is no longer available.
								</p>
							)}
						</CardBody>
					</Card>

					<CallStatusCard
						state={
							approval.state === 'pending' && !decidable ? 'expired' : approval.state
						}
						executionStatus={approval.execution_status}
						executionId={approval.execution_id}
						executionLink={approval._links.execution}
					/>

					{decidable && decideForbidden ? (
						<Card>
							<CardHeader>
								<CardTitle>Decision</CardTitle>
							</CardHeader>
							<CardBody>
								<p role="alert" className="text-muted-foreground text-sm">
									You can see this call but can&apos;t decide it. Only the
									agent&apos;s owner or an org admin can approve or deny it.
								</p>
							</CardBody>
						</Card>
					) : decidable ? (
						<Card>
							<CardHeader>
								<CardTitle>Decision</CardTitle>
							</CardHeader>
							<CardBody className="flex flex-col gap-4">
								<div>
									<Label htmlFor="reason">Reason (optional)</Label>
									<Textarea
										id="reason"
										placeholder="Recorded with your decision…"
										value={reason}
										onChange={(e) => setReason(e.target.value)}
										rows={3}
										maxLength={500}
										className="mt-1"
									/>
								</div>
								<div className="flex gap-3">
									<Button
										type="button"
										variant="danger"
										onClick={() => handleDecide(ApprovalDecision.DENY)}
										disabled={decide.isPending}
									>
										Deny
									</Button>
									<Button
										type="button"
										variant="primary"
										onClick={() => handleDecide(ApprovalDecision.APPROVE)}
										disabled={decide.isPending}
									>
										Approve and run
									</Button>
								</div>
							</CardBody>
						</Card>
					) : (
						approval.decided_at && (
							<Card>
								<CardHeader>
									<CardTitle>Outcome</CardTitle>
								</CardHeader>
								<CardBody>
									<dl className="grid gap-4 sm:grid-cols-2">
										<DetailRow
											label="Decided by"
											value={
												approval.decided_by ? (
													<Mono>{approval.decided_by}</Mono>
												) : (
													'—'
												)
											}
										/>
										<DetailRow
											label="Decided at"
											value={new Date(approval.decided_at).toLocaleString()}
										/>
										{approval.decision_reason && (
											<DetailRow
												label="Reason"
												value={approval.decision_reason}
											/>
										)}
									</dl>
								</CardBody>
							</Card>
						)
					)}
				</>
			)}
		</PageShell>
	);
}
