/**
 * Approval detail page — `/app/agents/approvals/:id`, a subsection of Agents.
 *
 * The review page an agent's held call links to (`GET /executions/holds/{id}`).
 * Laid out like Monitor's execution trace: the call first (what it does, on
 * which API, for which agent and credential, how long it has left), then the
 * decision, then the exact request that runs if approved. Everything is named
 * for a person — agents, owners, credentials and reviewers by name, times as
 * "5 min ago" — and the raw ids stay reachable only through "Copy reference".
 *
 * A pending, unexpired held execution offers Approve and Deny to anyone who
 * can see it; the server decides who may act (the agent's owner or an org
 * admin) and a refused decision (403) is explained in place. A decided one is
 * read-only. A signed-in user who cannot see it (404) is told who can.
 */
import { useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { ArrowLeft, ArrowUpRight, CheckSquare } from 'lucide-react';
import {
	AppLink,
	Button,
	CopyButton,
	EmptyState,
	Label,
	MethodBadge,
	PageHeader,
	PageShell,
	SkeletonRows,
	Textarea,
	Tooltip,
	VendorIcon,
} from '@/shared/ui';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';
import { useOptionalCurrentUser } from '@/shared/auth';
import { useCredential } from '@/shared/credentials/api';
import { useActorDirectory } from '@/shared/hooks';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
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
import {
	ReviewPanel,
	ReviewRow,
	ReviewSection,
} from '@/modules/agents/components/approvals/ReviewLayout';
import { isDecidable } from '@/modules/agents/lib/approvalState';

/** Pretty-print a JSON body; anything else is shown verbatim. */
function formatBody(body: string): string {
	try {
		return JSON.stringify(JSON.parse(body), null, 2);
	} catch {
		return body;
	}
}

/** "in 23 h", "in 40 min", or "Expired" — how long a pending call has left. */
function expiresIn(iso: string): string {
	const minutes = Math.round((Date.parse(iso) - Date.now()) / 60_000);
	if (Number.isNaN(minutes)) return '—';
	if (minutes <= 0) return 'Expired';
	if (minutes < 60) return `in ${minutes} min`;
	const hours = Math.floor(minutes / 60);
	return hours < 48 ? `in ${hours} h` : `in ${Math.floor(hours / 24)} days`;
}

/**
 * Names for the people on the page: "You" for the signed-in viewer, the
 * directory's name otherwise, and a plain label when neither is known — never
 * the raw id.
 */
function useActorNames(ids: (string | null | undefined)[]) {
	const viewer = useOptionalCurrentUser()?.id;
	const { resolve } = useActorDirectory(ids);
	return (id: string | null | undefined, fallback: string): string => {
		if (!id) return fallback;
		if (id === viewer) return 'You';
		return resolve(id) ?? fallback;
	};
}

/** "5 min ago" from the shared compact age ("5m"). */
function ago(iso: string): string {
	const age = timeAgo(iso);
	return age === 'now' ? 'just now' : `${age} ago`;
}

export default function ApprovalDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const [reason, setReason] = useState('');
	const [showBody, setShowBody] = useState(true);

	const { data: approval, isLoading, error } = useApproval(id);
	const decide = useDecideApproval();
	// The credential's name; a reviewer who can't read it sees a plain label.
	const credential = useCredential(approval?.credential_id);
	const nameOf = useActorNames([
		approval?.agent_id,
		approval?.agent_owner_id,
		approval?.decided_by,
	]);

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

	const shownState =
		approval && approval.state === 'pending' && !decidable ? 'expired' : approval?.state;
	const agentName = approval?.agent_name ?? nameOf(approval?.agent_id, 'An agent');

	return (
		<PageShell width="reading">
			<PageHeader
				title="Review held call"
				subtitle={
					approval
						? `${agentName} wants to make this call. It runs once if approved.`
						: "An agent's call is waiting on a human decision."
				}
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
				<div className="space-y-6">
					<ReviewSection title="Held call">
						<ReviewPanel>
							<div className="flex items-start gap-2">
								<MethodBadge method={approval.method} />
								<p className="text-foreground min-w-0 flex-1 font-mono text-[13px] break-all">
									{approval.path}
								</p>
								{shownState && <ApprovalStateBadge state={shownState} />}
							</div>
							<div className="mt-3">
								<ReviewRow
									label="API"
									value={
										<span className="inline-flex items-center gap-1.5">
											<VendorIcon
												name={approval.api_name}
												vendor={approval.api_vendor}
												size="sm"
												className="h-5 w-5 rounded text-[7px]"
											/>
											{approval.api_name}
										</span>
									}
								/>
								<ReviewRow
									label="Agent"
									value={
										approval.agent_name ??
										nameOf(approval.agent_id, 'Unknown agent')
									}
								/>
								<ReviewRow
									label="Agent owner"
									value={
										approval.agent_owner_id
											? nameOf(approval.agent_owner_id, 'Unknown user')
											: 'No owner — org admins review'
									}
								/>
								<ReviewRow
									label="Credential"
									value={
										credential.data?.name ?? 'A credential bound to this agent'
									}
								/>
								<ReviewRow
									label="Requested"
									value={
										<Tooltip content={formatTimestamp(approval.created_at)}>
											{ago(approval.created_at)}
										</Tooltip>
									}
								/>
								{approval.state === 'pending' && (
									<ReviewRow
										label="Expires"
										value={
											<Tooltip content={formatTimestamp(approval.expires_at)}>
												{expiresIn(approval.expires_at)}
											</Tooltip>
										}
									/>
								)}
							</div>
						</ReviewPanel>
					</ReviewSection>

					<ReviewSection
						title="What it will send"
						action={
							approval.request?.body ? (
								<Button
									variant="ghost"
									size="xs"
									onClick={() => setShowBody((v) => !v)}
									aria-expanded={showBody}
								>
									{showBody ? 'Hide body' : 'Show body'}
								</Button>
							) : null
						}
					>
						{approval.request ? (
							<ReviewPanel className="flex flex-col gap-3">
								<p className="font-mono text-xs break-all">
									{approval.request.method} {approval.request.url}
								</p>
								{approval.request.body ? (
									showBody && (
										<pre
											aria-label="Request body"
											className="bg-surface-inset max-h-96 overflow-auto rounded p-3 font-mono text-xs"
										>
											{formatBody(approval.request.body)}
										</pre>
									)
								) : (
									<p className="text-muted-foreground text-xs">
										No request body.
									</p>
								)}
								{showBody && approval.request.body_truncated && (
									<p className="text-muted-foreground text-xs">
										The body is shortened for display; the full body runs if
										approved.
									</p>
								)}
							</ReviewPanel>
						) : (
							<p className="text-muted-foreground text-sm">
								The held request is no longer available.
							</p>
						)}
					</ReviewSection>

					{decidable && decideForbidden ? (
						<ReviewSection title="Your decision">
							<p role="alert" className="text-muted-foreground text-sm">
								You can see this call but can&apos;t decide it. Only the
								agent&apos;s owner or an org admin can approve or deny it.
							</p>
						</ReviewSection>
					) : decidable ? (
						<ReviewSection title="Your decision">
							<ReviewPanel className="flex flex-col gap-4">
								<div>
									<Label htmlFor="reason">Reason (optional)</Label>
									<Textarea
										id="reason"
										placeholder="Recorded with your decision…"
										value={reason}
										onChange={(e) => setReason(e.target.value)}
										rows={2}
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
							</ReviewPanel>
						</ReviewSection>
					) : null}

					{approval.state !== 'pending' && (
						<CallStatusCard
							state={shownState ?? approval.state}
							executionStatus={approval.execution_status}
							executionId={approval.execution_id}
							executionLink={approval._links.execution}
						/>
					)}

					{approval.decided_at && approval.state !== 'pending' && (
						<ReviewSection title="Outcome">
							<ReviewPanel>
								<ReviewRow
									label="Decided by"
									value={nameOf(approval.decided_by, 'Unknown user')}
								/>
								<ReviewRow
									label="When"
									value={
										<Tooltip content={formatTimestamp(approval.decided_at)}>
											{ago(approval.decided_at)}
										</Tooltip>
									}
								/>
								{approval.decision_reason && (
									<ReviewRow label="Reason" value={approval.decision_reason} />
								)}
							</ReviewPanel>
						</ReviewSection>
					)}

					<footer className="text-muted-foreground flex flex-wrap items-center justify-between gap-2 text-xs">
						<p>
							An Ask rule on this agent&apos;s access to {approval.api_name} holds
							calls like this one for review.{' '}
							<AppLink
								href={ROUTE_PATHS.agentTab(approval.agent_id)}
								className="text-primary inline-flex items-center gap-0.5 font-medium hover:underline"
							>
								Review the agent&apos;s rules
								<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
							</AppLink>
						</p>
						<span className="inline-flex items-center gap-1">
							Copy reference
							<CopyButton
								value={approval.id}
								size="icon"
								variant="ghost"
								className="h-6 w-6 shrink-0"
								ariaLabel="Copy held execution reference"
								toastMessage="Reference copied"
							/>
						</span>
					</footer>
				</div>
			)}
		</PageShell>
	);
}
