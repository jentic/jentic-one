/**
 * PendingApprovalBanner — the flat surface's approval callout, above the agent
 * strip (plan §4.10, D15 + D16). An agent that self-registers via
 * `jentic register` leaves a person blocked in a terminal; this banner is what
 * gets them unblocked, so it must stay prominent without owning a table.
 *
 * One banner names the LONGEST-waiting pending agent — the backend orders
 * `list_all` by `created_at DESC`, so that is the LAST row of the (fully
 * drained) pending list, picked deliberately — and shows a live elapsed wait
 * ("waiting 4m") so the human cost is visible (D16). The clock is a local
 * ~30s display tick recomputed from `created_at` on every render (no drift
 * after a tab sleep); it is NOT a shortened refetch interval. Extra pending
 * agents fold into an "and N more waiting" count rather than stacking banners
 * (risk O8). While the pending list is still a floor (`atLeast` — the cursor
 * drain is incomplete or a later page failed) the banner stays honest: it
 * names the longest-waiting agent LOADED SO FAR (still a real, decidable
 * agent — D17 keeps the surface functional) and hedges the count as "N+ more
 * waiting", exactly like the nav badge's "N+".
 *
 * Actions keep the old band's one-click triage: `Review` selects the agent in
 * the strip (its panel shows Approve adjacent, D7), `Approve` fires
 * immediately, `Deny` routes to the page's reason-required dialog. When
 * nothing is pending the component renders NOTHING — zero reserved space (O8).
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
	/** The pending list is a floor (drain incomplete or a later page failed):
	 * hedge the "and N more waiting" count as "N+" and treat the pick as the
	 * longest-waiting loaded SO FAR, not a proven superlative. */
	atLeast: boolean;
	/** Select the agent in the strip (`?agent=<id>`) for review. */
	onReview: (id: string) => void;
	onApprove: (id: string) => void;
	/** Open the page's reason-required deny dialog. */
	onDeny: (agent: { id: string; name: string }) => void;
	/** The agent id with an approve in flight, if any (per-id scoping). */
	approvePendingId: string | null;
}

/**
 * Honest elapsed-wait copy across magnitudes. Below a minute the display tick
 * (~30s) cannot honestly animate seconds, so it says "under a minute" instead
 * of a frozen seconds figure; from minutes up it reuses the shared `timeAgo`
 * magnitudes ("4m", "3h", "2d", …).
 */
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

	// Trust but verify: the query asks for `status=pending`, and the filter
	// keeps a stale or overridden response from ever naming a non-pending
	// agent (no flash of wrong content while caches settle).
	const rows = pending.filter((a) => a.status === 'pending');

	// Longest-waiting = the LAST row. The backend serves ONE `created_at DESC`
	// sequence through the cursor, so every page-N+1 row is older than every
	// page-N row and the flattened drained list stays DESC end to end — the
	// last row is the oldest loaded. Only when the list is complete is that
	// provably the longest-waiting overall; while it is a floor (`atLeast`)
	// it is the longest-waiting so far, and the copy below hedges the count.
	const longest = rows.length > 0 ? rows[rows.length - 1] : undefined;
	const others = rows.length - 1;

	// Honesty (D17): "and N more waiting" only claims what the data proves.
	// A floor hedges as "N+" (mirroring the nav badge); a floor with nothing
	// else loaded still says "more" — the incomplete drain proves more exist.
	const moreWaiting = atLeast
		? others > 0
			? `and ${others}+ more waiting`
			: 'and more waiting'
		: others > 0
			? `and ${others} more waiting`
			: null;

	// D16 live clock: a local display tick. The label recomputes from
	// `created_at` on each render, so a slept tab snaps to the truth on the
	// next tick instead of accumulating drift. Cleaned up on unmount and torn
	// down entirely while nothing is pending.
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
