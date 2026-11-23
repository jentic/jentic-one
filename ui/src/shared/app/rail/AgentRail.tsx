/**
 * AgentRail — the docked rail: a persistent right-side surface on every
 * authenticated page at `xl+`, with two tabs — **Inbox** (the held calls
 * waiting on the viewer) and Activity (the REAL platform event feed,
 * `/events` + `/events/stream` SSE via the `agentStream` provider). Below
 * `xl` the same body opens in a drawer from the top bar (`ActivityDrawer`).
 *
 * Collapsed by default: the rail is an opt-in surface, not page chrome that
 * competes with the content. The strip stays visible so it's one click away —
 * Inbox (with its waiting count) above Activity — and the rail remembers
 * whether it was open and on which tab (`useRailView`).
 *
 * Owns only what is specific to the docked surface: the collapsed strip, the
 * open/close motion, focus across the swap, and `?inbox=1` at `xl+`. The
 * route → lens default and the failure chime run shell-wide
 * (`ShellActivityEffects`); the attention toast beside the strip is
 * `InboxToast`.
 *
 * Everything inside the rail is `ActivityRailBody`, shared with the drawer.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronLeft, ShieldQuestion } from 'lucide-react';
import { ActivityRailBody, useScopedActivity } from '@/shared/app/rail/ActivityRailBody';
import { LiveDot } from '@/shared/app/rail/LiveDot';
import { useRailFocusRequests, useRailView, type RailTab } from '@/shared/app/rail/railPreferences';
import { useInboxParam } from '@/shared/app/rail/useInboxParam';
import { activityStreamVtStyle } from '@/shared/app/viewTransitions';
import { usePendingApprovalsCount } from '@/shared/approvals';
import { ATTENTION_COUNT_CLASS } from '@/shared/approvals/attentionTone';
import { cn } from '@/shared/lib/utils';

const STRIP_PX = 40;
const OPEN_PX = 288;
/** A drawer ease: quick off the mark, long soft landing. */
const EASE = [0.32, 0.72, 0, 1] as const;

/** The controls focus lands on across the collapse/expand swap. */
const STRIP_ENTRY: Record<RailTab, string> = {
	inbox: 'button[data-rail-entry="inbox"]',
	activity: 'button[aria-label^="Show live activity"]',
};
const COLLAPSE_BUTTON = 'button[aria-label^="Collapse "]';
const tabSelector = (tab: RailTab) => `[role="tab"][data-rail-tab="${tab}"]`;

/**
 * The collapsed strip: Inbox above Activity.
 *
 *   • Inbox — the shield (amber while anything waits), the waiting count, and
 *     the word "Inbox". Opens the rail on its Inbox tab.
 *   • Activity — a live dot and the word "Activity"; no counts, it only says
 *     the stream is alive (and flags a failure you haven't seen). Opens the
 *     rail on its Activity tab.
 */
function CollapsedStrip({ onExpand }: { onExpand: (tab: RailTab) => void }) {
	const { failureCount, status, paused } = useScopedActivity();
	const { count: waiting, atLeast } = usePendingApprovalsCount();
	const parts = [
		failureCount > 0 ? `${failureCount} recent failure${failureCount === 1 ? '' : 's'}` : null,
		status === 'error' ? 'reconnecting' : null,
		status === 'forbidden' ? 'no access' : null,
		paused ? 'paused' : null,
	].filter(Boolean);
	const label = `Show live activity${parts.length ? ` (${parts.join(', ')})` : ''}`;
	const waitingLabel = atLeast ? `${waiting}+` : String(waiting);
	const inboxLabel = waiting > 0 ? `Inbox, ${waitingLabel} waiting for you` : 'Inbox';
	return (
		<div className="flex h-full w-full flex-col">
			<button
				type="button"
				data-rail-entry="inbox"
				onClick={() => onExpand('inbox')}
				aria-label={inboxLabel}
				title={inboxLabel}
				className="group hover:bg-surface-1-hover border-hairline-row flex w-full flex-col items-center gap-2 border-b py-2.5 transition-colors"
			>
				<ShieldQuestion
					aria-hidden="true"
					className={cn(
						'h-4 w-4',
						waiting > 0 ? 'text-warning' : 'text-muted-foreground',
					)}
				/>
				{/* The word precedes the count in the DOM, so the name ("Inbox, 3
				    waiting for you") holds the visible text in order; `order`
				    still stacks the count above the word. */}
				<span className="text-foreground-sub group-hover:text-foreground order-2 text-[11px] font-semibold transition-colors [writing-mode:vertical-rl]">
					Inbox
				</span>{' '}
				{waiting > 0 && (
					<span
						aria-hidden="true"
						className={cn(
							'order-1 grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-bold tabular-nums',
							ATTENTION_COUNT_CLASS,
						)}
					>
						{waitingLabel}
					</span>
				)}
			</button>
			<button
				type="button"
				onClick={() => onExpand('activity')}
				aria-label={label}
				title={label}
				className="group hover:bg-surface-1-hover flex w-full flex-1 flex-col items-center gap-2.5 py-2.5 transition-colors"
			>
				<ChevronLeft className="text-muted-foreground group-hover:text-foreground h-4 w-4 transition-transform duration-200 group-hover:-translate-x-0.5" />
				<LiveDot
					tone={
						failureCount > 0
							? 'failure'
							: status === 'live' && !paused
								? 'live'
								: status === 'error'
									? 'warning'
									: 'idle'
					}
				/>
				<span className="text-muted-foreground group-hover:text-foreground text-[11px] font-medium transition-colors [writing-mode:vertical-rl]">
					Activity
				</span>
			</button>
		</div>
	);
}

export function AgentRail() {
	const reduce = useReducedMotion();
	const { collapsed, setCollapsed, tab, setTab } = useRailView();
	// Expand and Collapse each take away the button that was pressed; hand
	// focus to the control that replaces it rather than dropping it to <body>.
	// An effect, not a mount ref: AnimatePresence re-uses a side that is still
	// exiting when you toggle back quickly, so it may never re-mount.
	const asideRef = useRef<HTMLElement | null>(null);
	const [focusTarget, setFocusTarget] = useState<{ selector: string } | null>(null);
	useEffect(() => {
		if (!focusTarget) return;
		asideRef.current
			?.querySelector<HTMLElement>(focusTarget.selector)
			?.focus({ preventScroll: true });
	}, [focusTarget]);

	function expand(on: RailTab) {
		setTab(on);
		setCollapsed(false);
		setFocusTarget({ selector: COLLAPSE_BUTTON });
	}
	function collapse() {
		setCollapsed(true);
		setFocusTarget({ selector: STRIP_ENTRY[tab] });
	}

	// The attention toast's "Open inbox" asks for the tab itself: the button
	// that was pressed leaves with the toast.
	useRailFocusRequests(
		useCallback((on: RailTab) => setFocusTarget({ selector: tabSelector(on) }), []),
	);

	// `?inbox=1` at `xl+`. Focus follows only when nothing else holds it (the
	// bell's link closes its menu and takes the focused row with it).
	useInboxParam('docked', () => {
		setTab('inbox');
		setCollapsed(false);
		if (!document.activeElement || document.activeElement === document.body) {
			setFocusTarget({ selector: tabSelector('inbox') });
		}
	});

	// One aside for both states: its width glides between the strip and the
	// open rail, so the page beside it reflows with it instead of cutting (or
	// cross-fading two layouts). The contents are laid out at their FINAL
	// width and clipped by the moving edge — the body slides in with the edge
	// rather than squashing — and cross-fade as they swap. The aside also
	// carries the stream's view-transition name, so leaving for Monitor
	// morphs whichever is showing into the Live activity panel there.
	return (
		<motion.aside
			ref={asideRef}
			aria-label="Activity"
			style={activityStreamVtStyle}
			initial={false}
			animate={{ width: collapsed ? STRIP_PX : OPEN_PX }}
			transition={reduce ? { duration: 0 } : { duration: 0.42, ease: EASE }}
			// `relative` is load-bearing: feed rows carry `sr-only` (absolutely
			// positioned) spans, and absolute boxes are only clipped by ancestors
			// in their CONTAINING-BLOCK chain — the static `overflow-hidden` here
			// and the feed's `overflow-y-auto` don't qualify. Without a positioned
			// ancestor those spans escaped to the sticky wrapper, adding ~240px of
			// phantom document scroll on short pages, which in turn dragged the
			// whole rail up with the scroll (the sticky wrapper is clamped to its
			// row, and the row only grows with real `main` content). See #1318
			// review follow-up: rail scrolled away on Settings/Toolkits.
			className="bg-surface-sheet border-hairline-field relative hidden shrink-0 overflow-hidden border-l xl:block"
		>
			<AnimatePresence initial={false}>
				{collapsed ? (
					<motion.div
						key="strip"
						className="absolute inset-y-0 left-0"
						style={{ width: STRIP_PX }}
						initial={{ opacity: 0 }}
						animate={{
							opacity: 1,
							transition: reduce ? { duration: 0 } : { duration: 0.2, delay: 0.18 },
						}}
						exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.1 } }}
					>
						<CollapsedStrip onExpand={expand} />
					</motion.div>
				) : (
					<motion.div
						key="body"
						className="absolute inset-y-0 left-0 flex flex-col"
						style={{ width: OPEN_PX }}
						initial={{ opacity: 0, x: 12 }}
						animate={{
							opacity: 1,
							x: 0,
							transition: reduce
								? { duration: 0 }
								: { duration: 0.34, delay: 0.08, ease: EASE },
						}}
						exit={{
							opacity: 0,
							x: 12,
							transition: { duration: reduce ? 0 : 0.16, ease: 'easeIn' },
						}}
					>
						<ActivityRailBody variant="rail" onCollapse={collapse} />
					</motion.div>
				)}
			</AnimatePresence>
		</motion.aside>
	);
}
