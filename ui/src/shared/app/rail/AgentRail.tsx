/**
 * AgentRail — the docked "Activity" rail: a persistent right-side surface on
 * every authenticated page at `xl+`, backed by the REAL platform event feed
 * (`/events` + `/events/stream` SSE) via the `agentStream` provider. Below
 * `xl` the same body opens in a drawer from the top bar (`ActivityDrawer`).
 *
 * Collapsed by default: the rail is an opt-in live tail, not page chrome
 * that competes with the content. The strip stays visible so it's one click
 * away, and remembers the user's choice.
 *
 * Owns only what is specific to the docked surface: the collapsed state
 * (persisted to localStorage), the collapsed strip, and the open/close
 * motion. The route → lens default and the failure chime run shell-wide
 * (`ShellActivityEffects`).
 *
 * Everything inside the rail is `ActivityRailBody`, shared with the drawer.
 */
import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronLeft } from 'lucide-react';
import { ActivityRailBody, useScopedActivity } from '@/shared/app/rail/ActivityRailBody';
import { LiveDot } from '@/shared/app/rail/LiveDot';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import { readBool, writeBool } from '@/shared/app/rail/railPreferences';
import { activityStreamVtStyle } from '@/shared/app/viewTransitions';
import { RAIL_COLLAPSED_STORAGE_KEY } from '@/shared/lib/agentStream';

const STRIP_PX = 40;
const OPEN_PX = 288;
/** A drawer ease: quick off the mark, long soft landing. */
const EASE = [0.32, 0.72, 0, 1] as const;

/**
 * The collapsed strip: a live dot and the word "Activity". Deliberately no
 * counts — "needs you" is the Notifications bell's job; the strip only says
 * the stream is alive (and flags a failure you haven't seen).
 */
function CollapsedStrip({ onExpand }: { onExpand: () => void }) {
	const { failureCount, status, paused } = useScopedActivity();
	const parts = [
		failureCount > 0
			? `${failureCount} unacknowledged failure${failureCount === 1 ? '' : 's'}`
			: null,
		status === 'error' ? 'reconnecting' : null,
		paused ? 'paused' : null,
	].filter(Boolean);
	const label = `Show live activity${parts.length ? ` (${parts.join(', ')})` : ''}`;
	return (
		<button
			type="button"
			onClick={onExpand}
			aria-label={label}
			title={label}
			className="group hover:bg-background/40 flex h-full w-full flex-col items-center gap-2.5 py-2.5 transition-colors"
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
	);
}

export function AgentRail() {
	const reduce = useReducedMotion();
	const [collapsed, setCollapsed] = useState<boolean>(() =>
		readBool(RAIL_COLLAPSED_STORAGE_KEY, true),
	);
	useEffect(() => writeBool(RAIL_COLLAPSED_STORAGE_KEY, collapsed), [collapsed]);
	// Expand and Collapse each take away the button that was pressed; hand
	// focus to the control that replaces it rather than dropping it to <body>.
	// An effect, not a mount ref: AnimatePresence re-uses a side that is still
	// exiting when you toggle back quickly, so it may never re-mount.
	const asideRef = useRef<HTMLElement | null>(null);
	const moveFocus = useRef(false);
	function toggle(next: boolean) {
		moveFocus.current = true;
		setCollapsed(next);
	}
	useEffect(() => {
		if (!moveFocus.current) return;
		moveFocus.current = false;
		asideRef.current
			?.querySelector<HTMLElement>(
				collapsed
					? 'button[aria-label^="Show live activity"]'
					: 'button[aria-label="Collapse activity"]',
			)
			?.focus({ preventScroll: true });
	}, [collapsed]);

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
			className="bg-muted border-border relative hidden shrink-0 overflow-hidden border-l xl:block"
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
						<CollapsedStrip onExpand={() => toggle(false)} />
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
						<ActivityRailBody variant="rail" onCollapse={() => toggle(true)} />
					</motion.div>
				)}
			</AnimatePresence>
		</motion.aside>
	);
}

// Re-export RailEventRow so future consumers can import it from the rail barrel.
export { RailEventRow };
