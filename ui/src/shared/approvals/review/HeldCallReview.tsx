/**
 * The review pop-up on one held call (`?review=<heldId>`), over any page.
 *
 * A waiting call shows who is asking for what, the agent's reason, the exact
 * request, and the verbs: a deny reason (sent only with a deny), Deny and
 * "Approve and run". A "1 of N" stepper walks the viewer's waiting calls, the
 * open call's agent first. A decision moves straight on to the next waiting
 * call with a one-line receipt on top; the last one closes the pop-up with a
 * single toast. A decision that lost the race (409) moves on the same way,
 * saying someone else decided first.
 *
 * A call that can no longer be decided (expired, withdrawn, already decided)
 * shows its outcome without verbs; a refused decision (403) is explained in
 * place of them; a call the viewer can't see (404) says who can.
 *
 * Per-call state (the deny reason, a refusal, a failure) lives in parts keyed
 * by the held call's id, so moving to another call starts it fresh; the
 * receipt and the calls decided in this sitting live here and go when the
 * pop-up closes (the host unmounts it).
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useLocation, useSearchParams } from 'react-router';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button, Dialog, toast } from '@/shared/ui';
import { cn, isolateText } from '@/shared/lib/utils';
import { ROUTES } from '@/shared/app/routes';
import { useCredential } from '@/shared/credentials/api';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import { useNow } from '@/shared/app/rail/useNow';
import { usePendingApprovals } from '@/shared/approvals/api';
import { useCanDecideApprovals } from '@/shared/approvals/pendingApprovals';
import {
	ApprovalDecision,
	ApprovalsApiError,
	isAlreadySettled,
	isDecideForbidden,
	useApproval,
	useDecideApproval,
	useEnsureApproval,
} from '@/shared/approvals/hooks';
import { isDecidable } from '@/shared/approvals/approvalState';
import { useHeldCallHeadlines, type HeldCallDetail } from '@/shared/approvals/heldCall';
import { DecisionBar, type Decision } from '@/shared/approvals/review/DecisionBar';
import {
	AgentReason,
	CallHeader,
	RequestDetails,
	ReviewFooter,
} from '@/shared/approvals/review/HeldCallSections';
import { useDecidersLine } from '@/shared/approvals/review/people';
import {
	LoadFailedState,
	NotVisibleState,
	ReceiptLine,
	ReviewSkeleton,
	SettledCallState,
	type ReviewReceipt,
} from '@/shared/approvals/review/ReviewStates';
import { nextWaitingCall, reviewQueue } from '@/shared/approvals/review/reviewQueue';

/** Past this many waiting calls the stepper drops its dots and keeps the count. */
const MAX_STEPPER_DOTS = 12;

interface HeldCallReviewProps {
	heldId: string;
	/** Walk every agent's waiting calls, not just the open call's agent's. */
	allAgents?: boolean;
	/** Move the pop-up to another call (no new history entry). */
	onAdvance: (heldId: string) => void;
	onClose: () => void;
}

export function HeldCallReview({
	heldId,
	allAgents = false,
	onAdvance,
	onClose,
}: HeldCallReviewProps) {
	const { data: call, isLoading, error, refetch } = useApproval(heldId);
	const canDecide = useCanDecideApprovals();
	const pendingQuery = usePendingApprovals({ enabled: canDecide });
	const decide = useDecideApproval({ quiet: true });
	const ensureApproval = useEnsureApproval();
	const now = useNow();
	const [receipt, setReceipt] = useState<ReviewReceipt | null>(null);
	// Between a decision landing and the pop-up moving on: the next call is
	// being checked. The verbs stay busy, the pop-up stays put.
	const [advancing, setAdvancing] = useState<Decision | null>(null);
	// Decided in this sitting: the waiting list can still show them until it refreshes.
	const [settledIds, setSettledIds] = useState<ReadonlySet<string>>(() => new Set());
	const [refusedId, setRefusedId] = useState<string | null>(null);
	const [failure, setFailure] = useState<{ heldId: string; message: string } | null>(null);

	const pending = (pendingQuery.data?.approvals ?? []).filter((c) => !settledIds.has(c.id));
	const headlineOf = useHeldCallHeadlines(call ? [call, ...pending] : pending);
	const directory = useActorDirectory([call?.agent_id]);
	const credential = useCredential(call?.credential_id);
	const deciders = useDecidersLine(call);
	useAgentBehindReview(call?.agent_id);

	const ownName = call?.agent_name?.trim();
	const agentName = ownName
		? ownName
		: ((call ? directory.resolve(call.agent_id) : undefined) ?? 'The agent');
	// Against the clock, so an open pop-up sees its call's window close.
	const decidable = call ? isDecidable(call.state, call.expires_at, new Date(now)) : false;
	const notVisible = error instanceof ApprovalsApiError && error.status === 404;
	// The queue is the open call's agent's waiting calls, unless the surface
	// that opened the pop-up spoke for every agent (the attention toast).
	const scoped = !allAgents;
	const queue = call ? reviewQueue(pending, call.agent_id, scoped) : [];
	const position = queue.findIndex((c) => c.id === heldId);
	const inFlight =
		decide.isPending && decide.variables?.heldExecutionId === heldId
			? decide.variables.body.decision === ApprovalDecision.APPROVE
				? 'approve'
				: 'deny'
			: null;

	// The list can lag a call: one decided elsewhere, or gone, stays listed
	// until the next read. Seen settled (or not found) here, it leaves this
	// sitting's queue at once and the list is re-read, so the stepper never
	// counts it again.
	const { data: pendingData, refetch: refetchPending } = pendingQuery;
	const goneHere = notVisible || (!!call && !decidable);
	const refreshedFor = useRef<string | null>(null);
	useEffect(() => {
		if (!goneHere || refreshedFor.current === heldId) return;
		if (!pendingData?.approvals.some((c) => c.id === heldId)) return;
		refreshedFor.current = heldId;
		setSettledIds((prev) => new Set(prev).add(heldId));
		void refetchPending();
	}, [goneHere, heldId, pendingData, refetchPending]);

	/**
	 * After a decision on `decided`: on to the next waiting call, or close with
	 * one toast. Each candidate is re-read first: the list can lag, and a call
	 * decided elsewhere since its last read is skipped, never landed on.
	 */
	async function moveOn(decided: HeldCallDetail, done: ReviewReceipt, decision: Decision) {
		const skip = new Set(settledIds).add(decided.id);
		let next = nextWaitingCall(pending, decided, skip, scoped);
		setAdvancing(decision);
		try {
			while (next) {
				try {
					const fresh = await ensureApproval(next.id);
					if (isDecidable(fresh.state, fresh.expires_at)) break;
				} catch (error) {
					// Gone (404) is as good as settled. Any other failure is not: land
					// on the call and let the pop-up's own read say what is wrong.
					if (!(error instanceof ApprovalsApiError && error.status === 404)) break;
				}
				skip.add(next.id);
				next = nextWaitingCall(pending, decided, skip, scoped);
			}
		} finally {
			setAdvancing(null);
		}
		// Merged, not replaced: the clock may have settled more while this waited.
		setSettledIds((prev) => new Set([...prev, ...skip]));
		if (next) {
			setReceipt(done);
			onAdvance(next.id);
			return;
		}
		onClose();
		toast(receiptToast(done));
	}

	function handleDecide(target: HeldCallDetail, decision: Decision, reason: string | null) {
		const done = { heldId: target.id, headline: headlineOf(target) };
		setFailure(null);
		decide.mutate(
			{
				heldExecutionId: target.id,
				// The reason goes to the agent with a deny only.
				body:
					decision === 'deny'
						? { decision: ApprovalDecision.DENY, reason }
						: { decision: ApprovalDecision.APPROVE },
			},
			{
				onSuccess: (row) =>
					void moveOn(
						target,
						{ ...done, kind: row.state === 'approved' ? 'approved' : 'denied' },
						decision,
					),
				onError: (err) => {
					if (isAlreadySettled(err)) {
						// Settled first by someone else — or by the clock.
						const expired = Date.parse(target.expires_at) <= Date.now();
						void moveOn(
							target,
							{ ...done, kind: expired ? 'expired' : 'conflict' },
							decision,
						);
					} else if (isDecideForbidden(err)) setRefusedId(target.id);
					else setFailure({ heldId: target.id, message: err.message });
				},
			},
		);
	}

	/** Escape, the X and the backdrop: not while a decision is on its way. */
	function close() {
		if (decide.isPending || advancing) return;
		onClose();
	}

	function step(toId: string) {
		setReceipt(null);
		onAdvance(toId);
	}

	// What a settled call offers to move on to: the same queue, less itself.
	const waiting = queue.filter((c) => c.id !== heldId);

	let body: ReactNode;
	let footer: ReactNode;
	const closeButton = (
		<Button variant="secondary" size="md" onClick={close}>
			Close
		</Button>
	);
	if (isLoading) {
		body = <ReviewSkeleton />;
	} else if (notVisible) {
		body = <NotVisibleState />;
		footer = closeButton;
	} else if (error || !call) {
		body = (
			<LoadFailedState
				message={error?.message ?? 'The call could not be loaded.'}
				onRetry={() => void refetch()}
			/>
		);
		footer = closeButton;
	} else if (!decidable) {
		body = (
			<SettledCallState
				key={call.id}
				call={call}
				agentName={agentName}
				headline={headlineOf(call)}
				waitingCount={waiting.length}
				onShowWaiting={() => {
					const next = nextWaitingCall(pending, call, undefined, scoped);
					if (next) step(next.id);
				}}
			/>
		);
		footer = closeButton;
	} else {
		body = (
			<div key={call.id} className="space-y-4">
				<CallHeader
					call={call}
					agentName={agentName}
					headline={headlineOf(call)}
					credentialName={credential.data?.name ?? null}
					deciders={deciders}
				/>
				<AgentReason call={call} />
				<RequestDetails call={call} />
				<ReviewFooter call={call} />
			</div>
		);
		footer =
			refusedId === call.id ? (
				<div className="flex w-full flex-wrap items-center justify-between gap-3">
					<p role="alert" className="text-foreground-sub text-[13px]">
						You can see this call but can&apos;t decide it. Only the agent&apos;s owner
						or an org admin can approve or deny it.
					</p>
					{closeButton}
				</div>
			) : (
				<DecisionBar
					key={call.id}
					pending={inFlight ?? advancing}
					error={failure?.heldId === call.id ? failure.message : null}
					onDecide={(decision, reason) => handleDecide(call, decision, reason)}
				/>
			);
	}

	return (
		<Dialog
			open
			onClose={close}
			size="lg"
			title="Review call"
			subtitle={
				call && decidable && !error && position >= 0 ? (
					<ReviewStepper
						queue={queue}
						position={position}
						disabled={decide.isPending || advancing !== null}
						labelOf={headlineOf}
						onStep={step}
					/>
				) : undefined
			}
			footer={footer}
		>
			{receipt && (
				<div className="mb-4">
					<ReceiptLine receipt={receipt} onDismiss={() => setReceipt(null)} />
				</div>
			)}
			{body}
		</Dialog>
	);
}

function receiptToast(done: ReviewReceipt) {
	const headline = isolateText(done.headline);
	if (done.kind === 'expired') {
		return {
			title: `This call expired · ${headline}`,
			description: "It didn't run; your decision wasn't applied.",
			variant: 'info' as const,
		};
	}
	if (done.kind === 'conflict') {
		return {
			title: `Someone else decided first · ${headline}`,
			description: "Your decision wasn't applied.",
			variant: 'info' as const,
		};
	}
	return {
		title: `${done.kind === 'approved' ? 'Approved' : 'Denied'} · ${headline}`,
		variant: done.kind === 'approved' ? ('success' as const) : ('default' as const),
	};
}

/** "2 of 7 waiting for you", with previous / next and a dot per call. */
function ReviewStepper<T extends { id: string }>({
	queue,
	position,
	disabled,
	labelOf,
	onStep,
}: {
	queue: readonly T[];
	position: number;
	disabled: boolean;
	labelOf: (call: T) => string;
	onStep: (heldId: string) => void;
}) {
	const prev = queue[position - 1];
	const next = queue[position + 1];
	return (
		<span className="flex flex-wrap items-center gap-x-2 gap-y-1">
			<span>
				{position + 1} of {queue.length} waiting for you
			</span>
			{queue.length > 1 && (
				<span className="flex items-center gap-1">
					<Button
						variant="ghost"
						size="icon-xs"
						className="h-6 w-6"
						aria-label={prev ? `Previous: ${labelOf(prev)}` : 'Previous waiting call'}
						disabled={disabled || !prev}
						onClick={() => prev && onStep(prev.id)}
					>
						<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
					</Button>
					{queue.length <= MAX_STEPPER_DOTS && (
						<span className="flex gap-1" aria-hidden="true">
							{queue.map((c, i) => (
								<span
									key={c.id}
									className={cn(
										'h-1.5 w-4 rounded-full',
										i === position ? 'bg-primary' : 'bg-surface-tonal-hover',
									)}
								/>
							))}
						</span>
					)}
					<Button
						variant="ghost"
						size="icon-xs"
						className="h-6 w-6"
						aria-label={next ? `Next: ${labelOf(next)}` : 'Next waiting call'}
						disabled={disabled || !next}
						onClick={() => next && onStep(next.id)}
					>
						<ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
					</Button>
				</span>
			)}
		</span>
	);
}

/**
 * A review link opened on the Agents page with no agent selected lands on the
 * call's agent behind the pop-up (`?agent=`, replacing the entry). Decided as
 * the pop-up lands on each page (at open, or on a later navigation with it
 * open), so the page's own first-agent fallback can't pre-empt it.
 */
function useAgentBehindReview(agentId: string | undefined) {
	const { pathname } = useLocation();
	const [params, setParams] = useSearchParams();
	const hasAgent = params.has('agent');
	const decidedOn = useRef<string | null>(null);
	const wanted = useRef(false);
	useEffect(() => {
		if (decidedOn.current === pathname) return;
		decidedOn.current = pathname;
		wanted.current = pathname === ROUTES.agents && !hasAgent;
	}, [pathname, hasAgent]);
	useEffect(() => {
		if (!agentId || !wanted.current) return;
		wanted.current = false;
		setParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set('agent', agentId);
				return next;
			},
			{ replace: true },
		);
	}, [agentId, pathname, setParams]);
}
