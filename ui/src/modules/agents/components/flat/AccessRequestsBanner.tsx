/**
 * AccessRequestsBanner — the flat surface's active-access-request callout. An
 * agent that files an access request (via `jentic request`, or a runtime
 * provisioning plan) leaves a binding decision waiting on a human approver.
 *
 * One banner names the longest-waiting pending request with a live elapsed
 * wait; the rest fold into "and N more waiting". A single Review button opens
 * the shared decision dialog, which is where approve/deny actually happen — the
 * decision isn't a one-click mutation because a provisioning plan must be
 * fulfilled in the setup wizard before it can be approved, so surfacing bare
 * Approve/Deny here would be a false affordance. Nothing pending renders NOTHING.
 */
import { useEffect, useReducer } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ActorLabel, Button } from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { summarizeAccessRequest, type AccessRequest } from '@/shared/lib';

interface AccessRequestsBannerProps {
	/** Pending, viewer-actionable requests in any order — the banner picks the
	 * longest-waiting by `filed_at` itself. */
	requests: AccessRequest[];
	/** Open the shared decision dialog for a request — the surface where approve,
	 * deny, and (for a plan) the setup wizard live. */
	onReview: (request: AccessRequest) => void;
}

/** Elapsed-wait copy. Below a minute the ~30s display tick cannot honestly
 * animate seconds, so it says "under a minute"; from minutes up it reuses the
 * shared `timeAgo` magnitudes. */
export function accessRequestWaitingLabel(filedAt: string): string {
	const ms = Date.parse(filedAt);
	if (Number.isNaN(ms)) return 'waiting';
	const deltaSec = Math.floor((Date.now() - ms) / 1000);
	if (deltaSec < 60) return 'waiting under a minute';
	return `waiting ${timeAgo(filedAt)}`;
}

/** `Access to …` → `access to …`, so the summary reads mid-sentence. */
function lowerFirst(text: string): string {
	return text.charAt(0).toLowerCase() + text.slice(1);
}

export function AccessRequestsBanner({ requests, onReview }: AccessRequestsBannerProps) {
	const reducedMotion = useReducedMotion();

	// Trust but verify: the hook asks for `status=pending`, and the filter keeps a
	// stale response from ever naming a decided request.
	const rows = requests.filter((r) => r.status === 'pending');

	// Longest-waiting = the oldest `filed_at`. Own the ordering here rather than
	// leaning on the endpoint's default sort: the request has no order param, so a
	// server that returns ascending or unsorted would otherwise let us name the
	// newest request and fold the genuinely oldest into the "N more" count.
	const longest = rows.reduce<AccessRequest | undefined>(
		(oldest, r) =>
			oldest === undefined || Date.parse(r.filed_at) < Date.parse(oldest.filed_at)
				? r
				: oldest,
		undefined,
	);
	const others = rows.length - 1;
	const moreWaiting = others > 0 ? `and ${others} more waiting` : null;

	// A local display tick; the label recomputes from `filed_at` on each render,
	// so a slept tab snaps to the truth instead of accumulating drift.
	const [, tick] = useReducer((n: number) => n + 1, 0);
	const hasPending = Boolean(longest);
	useEffect(() => {
		if (!hasPending) return;
		const id = window.setInterval(tick, 30_000);
		return () => window.clearInterval(id);
	}, [hasPending]);

	return (
		<AnimatePresence initial={false}>
			{longest && (
				<motion.section
					key="access-requests-banner"
					role="region"
					aria-label="Access requests awaiting a decision"
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
							<ActorLabel
								actorId={longest.actor_id}
								className="font-heading font-semibold"
							/>{' '}
							<span className="text-muted-foreground">
								is asking for {lowerFirst(summarizeAccessRequest(longest))}
							</span>{' '}
							<span
								className="text-muted-foreground"
								title={`Filed ${formatTimestamp(longest.filed_at)}`}
							>
								· {accessRequestWaitingLabel(longest.filed_at)}
								{moreWaiting && <> · {moreWaiting}</>}
							</span>
						</p>
						<Button
							size="sm"
							variant="outline"
							onClick={() => onReview(longest)}
							aria-label={`Review access request from ${longest.actor_id}`}
						>
							Review
						</Button>
					</div>
				</motion.section>
			)}
		</AnimatePresence>
	);
}
