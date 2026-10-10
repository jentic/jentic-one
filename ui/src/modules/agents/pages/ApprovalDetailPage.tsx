/**
 * Approval detail page — `/app/agents/approvals/:id`, a subsection of Agents.
 *
 * The review page an agent's held call links to. Shows who filed it (agent and
 * owner), the matched rule, and the exact request that runs if approved
 * (method, URL, body). A pending, unexpired approval can be approved or denied;
 * a decided one is read-only. The page needs the reviewer's own sign-in; a
 * signed-in user who is not a reviewer for this approval is told who is.
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
import { JOBS_WRITE, useCanAccess } from '@/shared/auth';
import {
	ApprovalDecision,
	ApprovalsApiError,
	useApproval,
	useDecideApproval,
} from '@/modules/agents/api/approvals-hooks';
import { ApprovalStateBadge } from '@/modules/agents/components/approvals/ApprovalStateBadge';
import { ApprovalsHelp } from '@/modules/agents/components/approvals/ApprovalsHelp';
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
	// Deciding takes `jobs:write` (org admins hold it); a reviewer without it
	// still sees the held call but not the verbs.
	const canDecide = useCanAccess(JOBS_WRITE);
	const notReviewer = error instanceof ApprovalsApiError && error.status === 404;

	function handleDecide(decision: ApprovalDecision) {
		if (!id) return;
		decide.mutate(
			{ approvalId: id, body: { decision, reason: reason || null } },
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

					{decidable && !canDecide ? (
						<Card>
							<CardHeader>
								<CardTitle>Decision</CardTitle>
							</CardHeader>
							<CardBody>
								<p className="text-muted-foreground text-sm">
									Approving or denying a held call needs the{' '}
									<strong>jobs:write</strong> permission. Ask an org admin to
									decide it.
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
