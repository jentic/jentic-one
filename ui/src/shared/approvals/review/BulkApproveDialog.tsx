/**
 * The bulk-approve confirmation (`?review_batch=<id>,<id>…`): one line per
 * call (its headline, the agent and the API, and the amount where the request
 * carries one), a total only where the amounts share a currency, then
 * "Approve N".
 *
 * Deletes are never approved in bulk: any that reach this list are set aside
 * with a Review link that opens the single-call pop-up. Calls that stopped
 * waiting on the viewer since they were picked (decided, expired, or not theirs
 * to decide) are left out too.
 *
 * There is no batch endpoint, so "Approve N" decides one call at a time,
 * refreshes every surface once at the end, and reports once, in a toast: a call
 * someone else settled first counts as already decided, any other failure is
 * named and stays waiting.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Trash2 } from 'lucide-react';
import type { HeldExecutionResponse } from '@/shared/api';
import {
	Button,
	Dialog,
	MethodBadge,
	SectionLabel,
	UserText,
	VendorIcon,
	toast,
} from '@/shared/ui';
import { cn, isolateText } from '@/shared/lib/utils';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import { usePendingApprovals } from '@/shared/approvals/api';
import { useCanDecideApprovals } from '@/shared/approvals/pendingApprovals';
import {
	ApprovalDecision,
	isAlreadySettled,
	useApprovalDetails,
	useDecideApproval,
	useRefreshApprovalSurfaces,
} from '@/shared/approvals/hooks';
import { isDeleteCall, isRouteHeadline, useHeldCallHeadlines } from '@/shared/approvals/heldCall';
import { ReviewSkeleton } from '@/shared/approvals/review/ReviewStates';
import { bodyAmount, totalAmount, type BodyAmount } from '@/shared/approvals/review/reviewFormat';

interface BulkApproveDialogProps {
	heldIds: readonly string[];
	onClose: () => void;
	/** Open one call in the review pop-up (a delete left out of the batch). */
	onReviewOne: (heldId: string) => void;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function BulkApproveDialog({ heldIds, onClose, onReviewOne }: BulkApproveDialogProps) {
	const canDecide = useCanDecideApprovals();
	const { data, isLoading } = usePendingApprovals({ enabled: canDecide });
	const decide = useDecideApproval({ quiet: true, batch: true });
	const refresh = useRefreshApprovalSurfaces();
	// The calls being approved, fixed when "Approve N" is pressed: the waiting
	// list refreshes as each decision lands.
	const [running, setRunning] = useState<HeldExecutionResponse[] | null>(null);
	const [done, setDone] = useState(0);
	const busy = running !== null;
	// A run outlives the dialog if the host swaps it out (Back, another
	// surface's link): decisions already sent complete, and nothing here
	// touches a dialog that has gone.
	const alive = useRef(true);
	useEffect(() => {
		alive.current = true;
		return () => {
			alive.current = false;
		};
	}, []);

	const { calls, deletes, gone } = useMemo(() => {
		const byId = new Map((data?.approvals ?? []).map((c) => [c.id, c]));
		const picked = heldIds
			.map((id) => byId.get(id))
			.filter((c): c is HeldExecutionResponse => !!c);
		return {
			calls: picked.filter((c) => !isDeleteCall(c)),
			deletes: picked.filter(isDeleteCall),
			gone: data ? heldIds.length - picked.length : 0,
		};
	}, [data, heldIds]);
	const shown = running ?? calls;

	// The details carry the amounts; the names come from the actor directory.
	// Held still while the run goes: each decision invalidates the approvals
	// root, and N lines re-read per decision would be N² reads.
	const details = useApprovalDetails(
		shown.map((c) => c.id),
		{ enabled: !busy },
	);
	const headlineOf = useHeldCallHeadlines([...shown, ...deletes]);
	const directory = useActorDirectory(shown.map((c) => c.agent_id));
	const agentNameOf = (call: HeldExecutionResponse, i: number) =>
		directory.resolve(call.agent_id) ?? details[i]?.data?.agent_name ?? 'An agent';
	const amounts: (BodyAmount | null)[] = shown.map((_, i) =>
		bodyAmount(details[i]?.data?.request?.body),
	);
	const total = totalAmount(amounts);
	const agents = [...new Set(shown.map(agentNameOf))];

	async function approveAll() {
		const batch = [...calls];
		setRunning(batch);
		let approved = 0;
		let already = 0;
		const failed: string[] = [];
		for (const [i, call] of batch.entries()) {
			if (alive.current) setDone(i);
			try {
				await decide.mutateAsync({
					heldExecutionId: call.id,
					body: { decision: ApprovalDecision.APPROVE },
				});
				approved++;
			} catch (error) {
				if (isAlreadySettled(error)) already++;
				else
					failed.push(
						error instanceof Error ? error.message : 'Failed to submit decision',
					);
			}
		}
		refresh();
		toast(batchToast(batch, headlineOf, { approved, already, failed }));
		if (alive.current) onClose();
	}

	const close = () => {
		// Decisions already sent can't be called back: stay until the run reports.
		if (!busy) onClose();
	};

	let body: ReactNode;
	if (isLoading) {
		body = <ReviewSkeleton />;
	} else if (shown.length === 0 && deletes.length === 0) {
		body = (
			<p className="text-foreground-sub py-4 text-center text-[13px]">
				Nothing left to approve: these calls were already decided or expired.
			</p>
		);
	} else {
		body = (
			<div className="space-y-4">
				{shown.length > 0 && (
					<div className="space-y-1">
						<SectionLabel as="h3" count={shown.length}>
							Approve together
						</SectionLabel>
						<ul aria-label="Calls to approve" className="divide-hairline-row divide-y">
							{shown.map((call, i) => (
								<BatchLine
									key={call.id}
									call={call}
									headline={headlineOf(call)}
									agentName={agentNameOf(call, i)}
									amount={amounts[i]}
								/>
							))}
						</ul>
						{total && (
							<p className="text-foreground-sub border-hairline-field mt-1 border-t pt-2 text-[12.5px]">
								Total{' '}
								<strong className="text-foreground-name font-mono font-semibold">
									{total.amount} {total.currency}
								</strong>{' '}
								across the {plural(total.count, 'call', 'calls')} that carry an
								amount, as sent
							</p>
						)}
					</div>
				)}
				{deletes.length > 0 && (
					<div className="bg-surface-field rounded-lg px-3.5 pt-2.5 pb-1">
						<p className="text-foreground-sub flex items-center gap-1.5 text-[12.5px] font-semibold">
							<Trash2 className="text-danger h-3.5 w-3.5" aria-hidden="true" />
							Left out: deletes are approved one at a time
						</p>
						<ul className="divide-hairline-row divide-y">
							{deletes.map((call) => (
								<li key={call.id} className="flex items-center gap-3 py-1.5">
									<MethodBadge method={call.method} />
									<span className="text-foreground-name min-w-0 flex-1 truncate text-[13px]">
										<UserText>{headlineOf(call)}</UserText>
									</span>
									<Button
										variant="tonal"
										size="xs"
										disabled={busy}
										onClick={() => onReviewOne(call.id)}
										aria-label={`Review ${headlineOf(call)}`}
									>
										Review
									</Button>
								</li>
							))}
						</ul>
					</div>
				)}
				{gone > 0 && (
					<p className="text-foreground-sub text-xs">
						{plural(gone, 'call you picked is', 'calls you picked are')} no longer
						waiting for you.
					</p>
				)}
			</div>
		);
	}

	const count = shown.length;
	return (
		<Dialog
			open
			onClose={close}
			size="lg"
			title={count > 0 ? `Approve ${plural(count, 'call', 'calls')}` : 'Approve calls'}
			subtitle={agents.length > 0 ? `From ${listNames(agents)}` : undefined}
			footer={
				count > 0 ? (
					<>
						<Button variant="ghost" size="md" disabled={busy} onClick={close}>
							Cancel
						</Button>
						<Button
							variant="primary"
							size="md"
							loading={busy}
							onClick={() => void approveAll()}
						>
							{busy ? `Approving ${done + 1} of ${count}…` : `Approve ${count}`}
						</Button>
					</>
				) : (
					<Button variant="secondary" size="md" onClick={close}>
						Close
					</Button>
				)
			}
		>
			{body}
		</Dialog>
	);
}

function BatchLine({
	call,
	headline,
	agentName,
	amount,
}: {
	call: HeldExecutionResponse;
	headline: string;
	agentName: string;
	amount: BodyAmount | null;
}) {
	const isRoute = isRouteHeadline(headline);
	return (
		<li className="flex items-center gap-3 py-2">
			<VendorIcon name={call.api_name} vendor={call.api_vendor} size="xs" />
			<div className="min-w-0 flex-1">
				<p
					className={cn(
						'text-foreground-name truncate text-[13px] font-semibold',
						isRoute && 'font-mono text-[12px]',
					)}
				>
					<UserText>{headline}</UserText>
				</p>
				<p className="text-foreground-sub truncate text-[12px]">
					<UserText>{agentName}</UserText> · <UserText>{call.api_name}</UserText>
				</p>
			</div>
			{amount && (
				<span className="text-foreground-sub shrink-0 font-mono text-[12px] tabular-nums">
					{amount.amount}
					{amount.currency ? ` ${amount.currency}` : ''}
				</span>
			)}
		</li>
	);
}

/** "billing-ops", "billing-ops and release-manager", "3 agents". */
function listNames(names: readonly string[]): string {
	if (names.length <= 2) return names.join(' and ');
	return `${names.length} agents`;
}

function batchToast(
	batch: readonly HeldExecutionResponse[],
	headlineOf: (call: HeldExecutionResponse) => string,
	{ approved, already, failed }: { approved: number; already: number; failed: string[] },
) {
	const total = batch.length;
	if (approved === total) {
		return {
			title:
				total === 1
					? `Approved · ${isolateText(headlineOf(batch[0]))}`
					: `Approved ${plural(total, 'call', 'calls')}`,
			variant: 'success' as const,
		};
	}
	const notes = [
		already > 0 &&
			`${plural(already, 'call was', 'calls were')} already decided by someone else.`,
		failed.length > 0 &&
			`${plural(failed.length, "call couldn't be approved and is", "calls couldn't be approved and are")} still waiting: ${failed[0]}`,
	].filter(Boolean);
	return {
		title:
			approved > 0
				? `Approved ${approved} of ${plural(total, 'call', 'calls')}`
				: 'No calls approved',
		description: notes.join(' '),
		variant: failed.length > 0 ? ('warning' as const) : ('info' as const),
	};
}
