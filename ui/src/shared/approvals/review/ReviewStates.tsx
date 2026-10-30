/**
 * The review pop-up's other states: loading, a call that can no longer be
 * decided (expired, withdrawn, or decided before the viewer opened it), a call
 * the viewer may not see, a load that failed, and the one-line receipt that
 * sits on top of the next call after a decision. All on neutral surfaces: the
 * colour is on the icon only.
 */
import type { ReactNode } from 'react';
import { Ban, Check, Clock3, EyeOff, RotateCw, TriangleAlert, Undo2, Users, X } from 'lucide-react';
import { Button, Skeleton, SkeletonRows, Tooltip, UserText } from '@/shared/ui';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import { useApproval } from '@/shared/approvals/hooks';
import { isDecidable } from '@/shared/approvals/approvalState';
import type { HeldCallDetail } from '@/shared/approvals/heldCall';
import { CallStatusCard } from '@/shared/approvals/review/CallStatusCard';
import { usePeopleNames } from '@/shared/approvals/review/people';
import { waitingSince } from '@/shared/approvals/heldCallCopy';
import { holdHours } from '@/shared/approvals/review/reviewFormat';

export function ReviewSkeleton() {
	return (
		<div className="space-y-4">
			<div className="space-y-2.5" aria-hidden="true">
				<Skeleton className="h-3.5 w-2/5" />
				<Skeleton className="h-6 w-4/5" />
				<Skeleton className="h-3 w-1/2" />
			</div>
			<Skeleton className="h-16 w-full rounded-lg" />
			<SkeletonRows rows={3} />
		</div>
	);
}

/** A centred icon, title and a line or two: the shape every unavailable state shares. */
function StateMessage({
	icon,
	title,
	children,
	action,
}: {
	icon: ReactNode;
	title: string;
	children: ReactNode;
	action?: ReactNode;
}) {
	return (
		<div className="flex flex-col items-center px-4 py-6 text-center">
			<span className="bg-surface-field text-foreground-sub mb-3 grid h-10 w-10 place-items-center rounded-full">
				{icon}
			</span>
			<p className="font-heading text-foreground-name text-base font-semibold">{title}</p>
			<div className="text-foreground-sub mt-1 max-w-sm space-y-2 text-[13px] leading-relaxed">
				{children}
			</div>
			{action && <div className="mt-4">{action}</div>}
		</div>
	);
}

/**
 * The server has no such call for this viewer: it no longer exists, or only the
 * agent's owner or an org admin may see it. The two read the same from here.
 */
export function NotVisibleState() {
	return (
		<StateMessage
			icon={<EyeOff className="h-5 w-5" aria-hidden="true" />}
			title="We can't find this call"
		>
			<p>
				It may no longer exist, or only its agent&apos;s owner or an org admin can see it.
			</p>
		</StateMessage>
	);
}

export function LoadFailedState({ message, onRetry }: { message: string; onRetry: () => void }) {
	return (
		<StateMessage
			icon={<TriangleAlert className="h-5 w-5" aria-hidden="true" />}
			title="Couldn't load this call"
			action={
				<Button variant="secondary" size="sm" onClick={onRetry}>
					<RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
					Try again
				</Button>
			}
		>
			<p>{message}</p>
		</StateMessage>
	);
}

/**
 * A call that can no longer be decided: it expired, the agent withdrew it, or
 * someone decided it before the viewer opened it (first decision wins). No
 * verbs; an approved call shows where it stands.
 */
export function SettledCallState({
	call,
	agentName,
	headline,
	waitingCount,
	onShowWaiting,
}: {
	call: HeldCallDetail;
	agentName: string;
	headline: string;
	/** Other calls still waiting on the viewer. */
	waitingCount: number;
	onShowWaiting: () => void;
}) {
	const nameOf = usePeopleNames([call.decided_by]);
	const expired =
		call.state === 'expired' ||
		(call.state === 'pending' && !isDecidable(call.state, call.expires_at));
	const approved = call.state === 'approved';
	const by = nameOf(call.decided_by, 'someone');
	const byLabel = by === 'You' ? 'you' : by;
	const hours = holdHours(call.created_at, call.expires_at);
	// A decided time the row doesn't state (or states unreadably) is left out.
	const decidedAt = call.decided_at && waitingSince(call.decided_at) ? call.decided_at : null;

	const action =
		waitingCount > 0 ? (
			<Button variant="secondary" size="sm" onClick={onShowWaiting}>
				See {waitingCount} waiting {waitingCount === 1 ? 'call' : 'calls'}
			</Button>
		) : undefined;
	const route = (
		<p className="text-foreground-faint font-mono text-[11.5px] break-all">
			<UserText>{headline}</UserText>
		</p>
	);

	if (expired) {
		return (
			<StateMessage
				icon={<Clock3 className="h-5 w-5" aria-hidden="true" />}
				title="Expired"
				action={action}
			>
				<p>
					Nobody decided {hours ? `within ${hours} h` : 'in time'}, so the call expired
					and didn&apos;t run. <UserText>{agentName}</UserText> can ask again if it still
					needs to.
				</p>
				{route}
			</StateMessage>
		);
	}
	if (call.state === 'withdrawn') {
		return (
			<StateMessage
				icon={<Undo2 className="h-5 w-5" aria-hidden="true" />}
				title="Withdrawn"
				action={action}
			>
				<p>
					<UserText>{agentName}</UserText> withdrew this call, so it didn&apos;t run.
				</p>
				{route}
			</StateMessage>
		);
	}
	if (call.state !== 'approved' && call.state !== 'denied') {
		// A state these surfaces don't know: say only what is sure.
		return (
			<StateMessage
				icon={<Clock3 className="h-5 w-5" aria-hidden="true" />}
				title="No longer waiting"
				action={action}
			>
				<p>This call isn&apos;t waiting for a decision any more.</p>
				{route}
			</StateMessage>
		);
	}
	return (
		<div className="space-y-4">
			<StateMessage
				icon={
					approved ? (
						<Check className="text-success h-5 w-5" aria-hidden="true" />
					) : (
						<Ban className="text-danger h-5 w-5" aria-hidden="true" />
					)
				}
				title={`${approved ? 'Approved' : 'Denied'} by ${byLabel}`}
				action={action}
			>
				<p>
					{decidedAt ? (
						<Tooltip content={formatTimestamp(decidedAt)}>
							Decided {waitingSince(decidedAt)}.
						</Tooltip>
					) : (
						'Already decided.'
					)}{' '}
					There&apos;s nothing left to do here.
				</p>
				{call.decision_reason && (
					<p className="text-foreground" dir="auto">
						“{call.decision_reason}”
					</p>
				)}
				{route}
			</StateMessage>
			{approved && (
				<CallStatusCard
					state={call.state}
					executionStatus={call.execution_status}
					executionId={call.execution_id}
					executionLink={call._links?.execution}
				/>
			)}
		</div>
	);
}

/**
 * What the viewer just did, shown on top of the next call. `conflict`: someone
 * else settled the call first; `expired`: its window closed before the
 * decision reached the server.
 */
export interface ReviewReceipt {
	kind: 'approved' | 'denied' | 'conflict' | 'expired';
	heldId: string;
	headline: string;
}

export function ReceiptLine({
	receipt,
	onDismiss,
}: {
	receipt: ReviewReceipt;
	onDismiss: () => void;
}) {
	return (
		<div
			role="status"
			className="bg-surface-field flex items-start gap-2.5 rounded-lg px-3 py-2"
		>
			{receipt.kind === 'conflict' || receipt.kind === 'expired' ? (
				<ConflictReceiptText receipt={receipt} />
			) : (
				<>
					<ReceiptIcon>
						{receipt.kind === 'approved' ? (
							<Check className="text-success h-3 w-3" aria-hidden="true" />
						) : (
							<Ban className="text-danger h-3 w-3" aria-hidden="true" />
						)}
					</ReceiptIcon>
					<p className="text-foreground-sub min-w-0 flex-1 text-[12.5px] leading-5">
						<span className="text-foreground-name font-semibold">
							{receipt.kind === 'approved' ? 'Approved' : 'Denied'}
						</span>{' '}
						· <UserText>{receipt.headline}</UserText>
					</p>
				</>
			)}
			<Button
				variant="ghost"
				size="icon-xs"
				className="-mt-0.5 -mr-1 h-6 w-6"
				aria-label="Dismiss"
				onClick={onDismiss}
			>
				<X className="h-3.5 w-3.5" aria-hidden="true" />
			</Button>
		</div>
	);
}

function ReceiptIcon({ children, className }: { children: ReactNode; className?: string }) {
	return (
		<span
			className={cn(
				'bg-surface-tonal mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full',
				className,
			)}
		>
			{children}
		</span>
	);
}

/**
 * Someone else settled the call first (409): the viewer's decision wasn't
 * applied. Re-reads the call to say who decided it, or that it expired or was
 * withdrawn.
 */
function ConflictReceiptText({ receipt }: { receipt: ReviewReceipt }) {
	const { data } = useApproval(receipt.heldId, { poll: false });
	const nameOf = usePeopleNames([data?.decided_by]);
	const state = data?.state;
	const title =
		receipt.kind === 'expired' || state === 'expired'
			? 'This call expired first'
			: state === 'withdrawn'
				? 'The agent withdrew this call first'
				: 'Someone else decided first';
	const by = nameOf(data?.decided_by, 'someone');
	const decidedAgo = data?.decided_at ? waitingSince(data.decided_at) : '';
	const who =
		(state === 'approved' || state === 'denied') && data?.decided_by
			? `${state === 'approved' ? 'Approved' : 'Denied'} by ${by === 'You' ? 'you' : by}${
					decidedAgo ? ` · ${decidedAgo}` : ''
				}`
			: null;
	return (
		<>
			<ReceiptIcon>
				<Users className="text-warning h-3 w-3" aria-hidden="true" />
			</ReceiptIcon>
			<p className="text-foreground-sub min-w-0 flex-1 text-[12.5px] leading-5">
				<span className="text-foreground-name block font-semibold">{title}</span>
				{who && <>{who} · </>}
				<UserText>{receipt.headline}</UserText> · your decision wasn&apos;t applied
			</p>
		</>
	);
}
