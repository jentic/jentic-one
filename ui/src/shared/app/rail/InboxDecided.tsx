/**
 * InboxDecided — the Inbox's Decided view: the held calls that no longer wait,
 * newest asked first, every one the viewer can see. A row says what was asked,
 * by which agent, how it ended and who ended it ("Approved by you · completed",
 * "Denied by Priya Shah", "Expired, nobody decided"), in the held-call
 * vocabulary of `approvalState`, with the deny reason under it. A press opens the call in the review pop-up, which shows a settled
 * call's outcome without verbs. "Show more" reads the next page.
 */
import { ChevronRight, History } from 'lucide-react';
import { Button, EmptyState, MethodBadge, UserText } from '@/shared/ui';
import {
	INBOX_EMPTY_CLASS,
	INBOX_MORE_CLASS,
	INBOX_ROW_CLASS,
	InboxLoadError,
	InboxLoading,
} from '@/shared/app/rail/inboxParts';
import { cn } from '@/shared/lib/utils';
import { useNow } from '@/shared/app/rail/useNow';
import {
	useCanDecideApprovals,
	useDecidedApprovals,
	useHeldCallHeadlines,
	useReviewLinks,
	type HeldCall,
} from '@/shared/approvals';
import {
	APPROVAL_STATE_LABELS,
	APPROVAL_STATE_VARIANT,
	CALL_STATUS_LABELS,
	CALL_STATUS_VARIANT,
	isDecidable,
	toCallStatus,
} from '@/shared/approvals/approvalState';
import { waitingSince } from '@/shared/approvals/heldCallCopy';
import { usePeopleNames } from '@/shared/approvals/review/people';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';

export type InboxDecidedProps = {
	/** Fired after a row opens a review (the drawer closes itself). */
	onNavigated?: () => void;
};

export function InboxDecided({ onNavigated }: InboxDecidedProps) {
	const canDecide = useCanDecideApprovals();
	const decided = useDecidedApprovals({ enabled: canDecide });
	const { calls } = decided;
	const headlineOf = useHeldCallHeadlines(calls);
	const agents = useActorDirectory(calls.map((call) => call.agent_id));
	const nameOf = usePeopleNames(calls.map((call) => call.decided_by));
	const { openReview } = useReviewLinks();

	if (!canDecide || (decided.isPending && !decided.data)) return <InboxLoading />;

	if (decided.isError && !decided.data) {
		return (
			<InboxLoadError
				title="Couldn't load the decided calls"
				error={decided.error}
				onRetry={() => void decided.refetch()}
				retrying={decided.isFetching}
			/>
		);
	}

	// Empty only when nothing is left to read: a read can find only waiting
	// calls and still have decided ones behind them, under Show more.
	if (calls.length === 0 && !decided.hasNextPage) {
		return (
			<EmptyState
				className={INBOX_EMPTY_CLASS}
				icon={<History className="text-foreground-faint h-5 w-5" aria-hidden="true" />}
				title="Nothing decided yet"
				description="Calls show up here once they are approved, denied, expire or are withdrawn."
			/>
		);
	}

	return (
		<div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
			<ul aria-label="Decided calls" className="pt-1">
				{calls.map((call) => (
					<DecidedRow
						key={call.id}
						call={call}
						headline={headlineOf(call)}
						agentName={agents.resolve(call.agent_id) ?? call.agent_id}
						decidedBy={nameOf(call.decided_by, 'someone')}
						onReview={() => {
							openReview(call.id);
							onNavigated?.();
						}}
					/>
				))}
			</ul>
			{decided.hasNextPage && (
				<Button
					variant="ghost"
					size="xs"
					className={INBOX_MORE_CLASS}
					loading={decided.isFetchingNextPage}
					onClick={() => void decided.fetchNextPage()}
				>
					Show more
				</Button>
			)}
			{/* Show more stays, to try again; a failed refresh keeps the rows. */}
			{decided.isFetchNextPageError && (
				<p className="text-foreground-faint px-2 pt-1 text-[11px]">
					Couldn&apos;t load more. Try again.
				</p>
			)}
		</div>
	);
}

type Variant = (typeof CALL_STATUS_VARIANT)[keyof typeof CALL_STATUS_VARIANT];

/** The text colour for a badge variant, for an outcome written as a line. */
const VARIANT_TEXT: Record<Variant, string> = {
	success: 'text-success',
	danger: 'text-danger',
	warning: 'text-warning',
	pending: 'text-primary',
	default: 'text-foreground-sub',
};

/**
 * How the call ended, who ended it, and when, in one line. An approved call
 * reads with where it went and takes that status's tone. A pending row past
 * its window is expired: the sweep records that shortly.
 */
function outcomeOf(
	call: HeldCall,
	decidedBy: string,
	now: number,
): { label: string; tone: Variant; at: string | null } {
	// Mid-line, the viewer is "you".
	const by = decidedBy === 'You' ? 'you' : decidedBy;
	const decidedAt = call.decided_at ?? call.updated_at ?? null;
	if (call.state === 'approved') {
		const status = toCallStatus(call.execution_status);
		// Held, just approved, is on its way like queued.
		const step = status === 'held' ? 'queued' : status;
		return {
			label: `${APPROVAL_STATE_LABELS.approved} by ${by}${step ? ` · ${CALL_STATUS_LABELS[step].toLowerCase()}` : ''}`,
			tone: step ? CALL_STATUS_VARIANT[step] : APPROVAL_STATE_VARIANT.approved,
			at: decidedAt,
		};
	}
	if (call.state === 'denied') {
		return {
			label: `${APPROVAL_STATE_LABELS.denied} by ${by}`,
			tone: APPROVAL_STATE_VARIANT.denied,
			at: decidedAt,
		};
	}
	if (call.state === 'withdrawn') {
		return {
			label: `${APPROVAL_STATE_LABELS.withdrawn} by the agent`,
			tone: APPROVAL_STATE_VARIANT.withdrawn,
			at: decidedAt,
		};
	}
	if (call.state === 'expired' || !isDecidable(call.state, call.expires_at, new Date(now))) {
		return {
			label: `${APPROVAL_STATE_LABELS.expired}, nobody decided`,
			tone: APPROVAL_STATE_VARIANT.expired,
			at: call.expires_at,
		};
	}
	return {
		label: APPROVAL_STATE_LABELS.pending,
		tone: APPROVAL_STATE_VARIANT.pending,
		at: null,
	};
}

function DecidedRow({
	call,
	headline,
	agentName,
	decidedBy,
	onReview,
}: {
	call: HeldCall;
	headline: string;
	agentName: string;
	decidedBy: string;
	onReview: () => void;
}) {
	const now = useNow();
	const outcome = outcomeOf(call, decidedBy, now);
	const when = outcome.at ? waitingSince(outcome.at, now) : '';
	const reason = call.state === 'denied' ? call.decision_reason?.trim() : '';
	// The `{' '}` between blocks draws nothing but keeps the words apart in
	// the control's name.
	return (
		<li>
			<button
				type="button"
				data-held-id={call.id}
				onClick={onReview}
				className={INBOX_ROW_CLASS}
			>
				<span className="sr-only">Review </span>
				<MethodBadge method={call.method} className="w-[46px]" />{' '}
				<span className="min-w-0 flex-1">
					<span className="text-foreground-name block truncate text-[12.5px] leading-[18px] font-semibold">
						<UserText>{headline}</UserText>
					</span>{' '}
					{/* The rail is narrow: how it ended has a line of its own. */}
					<span
						className={cn(
							'block truncate text-[11px] leading-4 font-medium',
							VARIANT_TEXT[outcome.tone],
						)}
					>
						{outcome.label}
					</span>{' '}
					<span className="text-foreground-faint block truncate text-[11px] leading-4">
						{when && `${when} · `}
						<UserText>{agentName}</UserText>
					</span>
					{reason && (
						<>
							{' '}
							<span className="text-foreground-sub line-clamp-2 text-[11px] leading-4 italic">
								“<UserText>{reason}</UserText>”
							</span>
						</>
					)}
				</span>
				<ChevronRight
					className="text-foreground-faint h-3.5 w-3.5 shrink-0"
					aria-hidden="true"
				/>
			</button>
		</li>
	);
}
