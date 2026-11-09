/**
 * InboxToast — the attention toast: bottom-right, beside the rail at `xl+`,
 * while held calls wait on the viewer. Two forms:
 *
 *   • summary — "7 calls are waiting for your approval", who is waiting
 *     ("billing-ops 4 · release-manager 3"), Review (the oldest call) and
 *     Open inbox;
 *   • arrival — when a call joins the waiting list: "New request · just now",
 *     "<agent> wants to run <headline>"; Review opens that call.
 *
 * Hidden while the rail is open on Inbox (the same list is on screen), while
 * nothing waits, and once dismissed — until the next call arrives. The
 * dismissal is the viewer's own for this browser session (sessionStorage):
 * it records the calls it covered, so any call it did not cover shows the
 * toast again.
 *
 * Arrivals are read off the waiting list itself: a call it lists that it did
 * not list before. The list refreshes the moment the live stream reports an
 * approval request (and on its own cadence without the stream), so a call
 * that arrived and was decided between two reads never flashes up.
 *
 * It sits beside whatever covers the right edge (the strip, the open Activity
 * rail, a docked column or sheet), and steps aside while that leaves the page
 * too narrow for it next to the app's own toasts (bottom-left, `ToastRegion`)
 * and a page's floating action dock (bottom-centre).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ShieldQuestion, X } from 'lucide-react';
import { Button, useRightEdgeInset } from '@/shared/ui';
import { openRailOn, useRailView } from '@/shared/app/rail/railPreferences';
import { useNow } from '@/shared/app/rail/useNow';
import {
	groupApprovalsByAgent,
	useCanDecideApprovals,
	useHeldCallHeadlines,
	usePendingApprovals,
	useReviewLinks,
	type HeldCall,
} from '@/shared/approvals';
import { ATTENTION_TILE_CLASS } from '@/shared/approvals/attentionTone';
import { heldCallAsk, stripBidiControls, waitingSince } from '@/shared/approvals/heldCallCopy';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import { useMediaQuery } from '@/shared/hooks/useMediaQuery';
import { cn } from '@/shared/lib/utils';

export const INBOX_TOAST_DISMISSED_KEY = 'j1.inboxToast.dismissedThrough';

/** The toast's gap to whatever covers the right edge. */
const EDGE_GAP_PX = 12;
/**
 * The narrowest page column the toast shares: room for the app's toasts on
 * the left (24rem plus gaps), a page's centred action dock, and the toast.
 */
const MIN_PAGE_PX = 1024;
/** Agents named on the summary line; the rest are a count. */
const SUMMARY_AGENTS = 3;
const EASE = [0.32, 0.72, 0, 1] as const;

/** The ids of the calls the last dismissal covered. */
function readDismissedIds(): ReadonlySet<string> {
	try {
		const raw = window.sessionStorage.getItem(INBOX_TOAST_DISMISSED_KEY);
		const parsed: unknown = raw == null ? [] : JSON.parse(raw);
		return new Set(
			Array.isArray(parsed)
				? parsed.filter((id): id is string => typeof id === 'string')
				: [],
		);
	} catch {
		return new Set();
	}
}

function writeDismissedIds(ids: ReadonlySet<string>): void {
	try {
		window.sessionStorage.setItem(INBOX_TOAST_DISMISSED_KEY, JSON.stringify([...ids]));
	} catch {
		// Storage blocked: the dismissal lasts until the page reloads.
	}
}

export function InboxToast() {
	const reduce = useReducedMotion();
	const canDecide = useCanDecideApprovals();
	const pending = usePendingApprovals({ enabled: canDecide });
	const calls = useMemo<HeldCall[]>(
		() => (canDecide ? (pending.data?.approvals ?? []) : []),
		[canDecide, pending.data],
	);
	const truncated = pending.data?.truncated ?? false;
	const groups = useMemo(() => groupApprovalsByAgent(calls), [calls]);
	const directory = useActorDirectory(groups.map((g) => g.agentId));
	const headlineOf = useHeldCallHeadlines(calls);
	const { openReview } = useReviewLinks();
	const { collapsed, tab } = useRailView();
	const railOnInbox = !collapsed && tab === 'inbox';
	const inset = useRightEdgeInset();
	const roomy = useMediaQuery(`(min-width: ${inset + MIN_PAGE_PX}px)`);
	const now = useNow();

	// A call the list names that its previous read did not is an arrival. The
	// first read only sets the baseline: what already waits is not "new". A list
	// cut short shifts at its edge between reads, so nothing it newly names is
	// an arrival either, only newly in view.
	const [arrivalId, setArrivalId] = useState<string | null>(null);
	const knownIds = useRef<Set<string> | null>(null);
	useEffect(() => {
		if (!pending.data || !canDecide) return;
		const known = knownIds.current;
		knownIds.current = new Set(calls.map((call) => call.id));
		if (!known || truncated) return;
		const arrived = calls.filter((call) => !known.has(call.id));
		if (arrived.length === 0) return;
		const newest = arrived.reduce((a, b) =>
			Date.parse(b.created_at) > Date.parse(a.created_at) ? b : a,
		);
		setArrivalId(newest.id);
	}, [calls, pending.data, canDecide, truncated]);

	// With the Inbox open, the arrival is on screen in the list already.
	useEffect(() => {
		if (railOnInbox) setArrivalId(null);
	}, [railOnInbox, arrivalId]);

	const [dismissedIds, setDismissedIds] = useState<ReadonlySet<string>>(() => readDismissedIds());
	const dismissed = calls.every((call) => dismissedIds.has(call.id));

	const arrival = arrivalId ? calls.find((call) => call.id === arrivalId) : undefined;
	const visible = calls.length > 0 && !railOnInbox && !dismissed && roomy;

	function dismiss() {
		const ids = new Set(calls.map((call) => call.id));
		writeDismissedIds(ids);
		setDismissedIds(ids);
		setArrivalId(null);
	}

	function review() {
		const target = arrival ?? calls[0];
		if (!target) return;
		setArrivalId(null);
		// The toast speaks for every agent, so its pop-up walks every waiting call.
		openReview(target.id, { all: true });
	}

	function openInbox() {
		setArrivalId(null);
		openRailOn('inbox', { focus: true });
	}

	const countLabel = truncated ? `${calls.length}+` : String(calls.length);
	const plural = calls.length !== 1 || truncated;
	const nameOf = (agentId: string) => stripBidiControls(directory.resolve(agentId) ?? agentId);
	const summary = [
		...groups.slice(0, SUMMARY_AGENTS).map((g) => `${nameOf(g.agentId)} ${g.approvals.length}`),
		...(groups.length > SUMMARY_AGENTS ? [`+${groups.length - SUMMARY_AGENTS} more`] : []),
	].join(' · ');

	return (
		<AnimatePresence>
			{visible && (
				<motion.section
					key={arrival ? `arrival-${arrival.id}` : 'summary'}
					aria-label="Calls waiting for your approval"
					data-testid="inbox-toast"
					data-form={arrival ? 'arrival' : 'summary'}
					initial={reduce ? false : { opacity: 0, y: 12 }}
					animate={{ opacity: 1, y: 0 }}
					exit={reduce ? undefined : { opacity: 0, y: 12 }}
					transition={{ duration: 0.3, ease: EASE }}
					style={{ right: inset + EDGE_GAP_PX }}
					className="border-border/60 bg-popover fixed bottom-5 z-[45] hidden w-[320px] rounded-xl border p-3 shadow-xl xl:block"
				>
					<div className="flex gap-2.5">
						<span
							aria-hidden="true"
							className={cn(
								'grid h-8 w-8 shrink-0 place-items-center rounded-lg',
								ATTENTION_TILE_CLASS,
							)}
						>
							<ShieldQuestion className="h-4 w-4" />
						</span>
						<div className="min-w-0 flex-1">
							<div role="status">
								{arrival ? (
									<>
										<p className="text-foreground-name text-[13px] leading-5 font-semibold">
											New request · {waitingSince(arrival.created_at, now)}
										</p>
										<p className="text-foreground-sub text-xs leading-[18px]">
											{heldCallAsk(
												nameOf(arrival.agent_id),
												headlineOf(arrival),
											)}
										</p>
									</>
								) : (
									<>
										<p className="text-foreground-name text-[13px] leading-5 font-semibold">
											{countLabel} {plural ? 'calls are' : 'call is'} waiting
											for your approval
										</p>
										<p className="text-foreground-sub truncate text-xs leading-[18px]">
											{summary}
										</p>
									</>
								)}
							</div>
							<div className="mt-2.5 flex gap-2">
								<Button variant="primary" size="xs" onClick={review}>
									Review
								</Button>
								<Button variant="secondary" size="xs" onClick={openInbox}>
									Open inbox
								</Button>
							</div>
						</div>
						<Button
							variant="ghost"
							size="icon-xs"
							aria-label="Dismiss"
							title="Dismiss"
							className="-mt-1 -mr-1 shrink-0"
							onClick={dismiss}
						>
							<X className="h-3.5 w-3.5" aria-hidden="true" />
						</Button>
					</div>
				</motion.section>
			)}
		</AnimatePresence>
	);
}
