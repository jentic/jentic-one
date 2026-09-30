/**
 * PendingApprovalBanner — the flat surface's approval callout. An agent that
 * self-registers via `jentic register` leaves a person blocked in a terminal.
 *
 * One banner names the longest-waiting pending agent with a live elapsed wait; the
 * rest fold into "and N more waiting". While the list is still a floor the count
 * hedges as "N+". Nothing pending renders NOTHING.
 */
import { useEffect, useReducer } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ActorStatusBadge, Button } from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { ACTION_LABEL, ACTION_VARIANT, type AgentEntity } from '@/modules/agents/api';

interface PendingApprovalBannerProps {
	/** Pending rows in backend order (`created_at DESC` — newest first),
	 * flattened across every drained cursor page. */
	pending: AgentEntity[];
	/** The pending list is a floor: hedge the count as "N+" and treat the pick as
	 * the longest-waiting loaded so far, not a proven superlative. */
	atLeast: boolean;
	/** Select the agent in the strip (`?agent=<id>`) for review. */
	onReview: (id: string) => void;
	onApprove: (id: string) => void;
	/** Open the page's reason-required deny dialog. */
	onDeny: (agent: { id: string; name: string }) => void;
	/** The agent id with an approve in flight, if any (per-id scoping). */
	approvePendingId: string | null;
}

/** Elapsed-wait copy. Below a minute the ~30s display tick cannot honestly
 * animate seconds, so it says "under a minute"; from minutes up it reuses the
 * shared `timeAgo` magnitudes. */
export function waitingLabel(createdAt: string): string {
	const ms = Date.parse(createdAt);
	if (Number.isNaN(ms)) return 'waiting';
	const deltaSec = Math.floor((Date.now() - ms) / 1000);
	if (deltaSec < 60) return 'waiting under a minute';
	return `waiting ${timeAgo(createdAt)}`;
}

export function PendingApprovalBanner({
	pending,
	atLeast,
	onReview,
	onApprove,
	onDeny,
	approvePendingId,
}: PendingApprovalBannerProps) {
	const reducedMotion = useReducedMotion();

	// Trust but verify: the query asks for `status=pending`, and the filter keeps a
	// stale response from ever naming a non-pending agent.
	const rows = pending.filter((a) => a.status === 'pending');

	// Longest-waiting = the LAST row: the backend serves one `created_at DESC`
	// sequence through the cursor, so the flattened list stays DESC end to end.
	// Provably the overall longest only once the list is complete.
	const longest = rows.length > 0 ? rows[rows.length - 1] : undefined;
	const others = rows.length - 1;

	// "and N more waiting" only claims what the data proves: a floor hedges as
	// "N+", and a floor with nothing else loaded still says "more".
	const moreWaiting = atLeast
		? others > 0
			? `and ${others}+ more waiting`
			: 'and more waiting'
		: others > 0
			? `and ${others} more waiting`
			: null;

	// A local display tick; the label recomputes from `created_at` on each render,
	// so a slept tab snaps to the truth instead of accumulating drift.
	const [, tick] = useReducer((n: number) => n + 1, 0);
	const hasPending = Boolean(longest);
	useEffect(() => {
		if (!hasPending) return;
		const id = window.setInterval(tick, 30_000);
		return () => window.clearInterval(id);
	}, [hasPending]);

	const busy = longest ? approvePendingId === longest.id : false;

	return (
		<AnimatePresence initial={false}>
			{longest && (
				<motion.section
					key="pending-approval-banner"
					role="region"
					aria-label="Awaiting approval"
					initial={reducedMotion ? false : { opacity: 0, height: 0 }}
					animate={{ opacity: 1, height: 'auto' }}
					exit={reducedMotion ? { opacity: 0 } : { opacity: 0, height: 0 }}
					transition={{ duration: reducedMotion ? 0 : 0.18, ease: 'easeOut' }}
					className="overflow-hidden"
				>
					<div className="border-warning/40 bg-warning/5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border px-3.5 py-2.5">
						<span
							className="bg-warning h-1.5 w-1.5 shrink-0 animate-pulse rounded-full motion-reduce:animate-none"
							aria-hidden="true"
						/>
						<p className="min-w-0 flex-1 basis-52 text-sm">
							<span className="font-heading font-semibold">{longest.name}</span>{' '}
							<ActorStatusBadge status="pending" className="mx-0.5 align-middle" />{' '}
							<span
								className="text-muted-foreground"
								title={`Registered ${formatTimestamp(longest.createdAt)}`}
							>
								{waitingLabel(longest.createdAt)} for approval
								{moreWaiting && <> · {moreWaiting}</>}
							</span>
						</p>
						<span className="flex items-center gap-2">
							<Button
								size="sm"
								variant="outline"
								disabled={busy}
								onClick={() => onReview(longest.id)}
								aria-label={`Review ${longest.name}`}
							>
								Review
							</Button>
							<Button
								size="sm"
								variant={ACTION_VARIANT.approve}
								disabled={busy}
								loading={busy}
								onClick={() => onApprove(longest.id)}
								aria-label={`${ACTION_LABEL.approve} ${longest.name}`}
							>
								{ACTION_LABEL.approve}
							</Button>
							<Button
								size="sm"
								variant={ACTION_VARIANT.deny}
								disabled={busy}
								onClick={() => onDeny({ id: longest.id, name: longest.name })}
								aria-label={`${ACTION_LABEL.deny} ${longest.name}`}
							>
								{ACTION_LABEL.deny}
							</Button>
						</span>
					</div>
				</motion.section>
			)}
		</AnimatePresence>
	);
}
