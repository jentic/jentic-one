/**
 * AgentStrip — the agent selector on the flat Agents surface ("pinned + linked
 * stack"). Under a quiet header ("Agents 38 · 2 waiting", "Switch agent ⌘K"):
 *
 *   - LEFT: the selected agent's tab, pinned on a raised layer and left out of
 *     the scroll list. It owns the notch into the agent card below, so the notch
 *     never moves with the rail. A switch flies the new tab into the pin and the
 *     old one back to its sorted place. A `‹ N` chip at the pin's edge counts
 *     the tabs scrolled behind it and pages back.
 *   - MIDDLE: the scrolling tabs — avatar, lifecycle glyph, name, API-count
 *     badge, "· N to set up". Waiting agents lead under "Waiting".
 *   - RIGHT: up to five circles for the next agents off-screen to the right, in
 *     order, then `+N` (the exact rest) — or, with nothing off to the right, a
 *     search button — opening the agent picker (also ⌘K / Ctrl+K).
 *
 * Tabs and circles show a hover card on hover and focus. The tabs form one
 * `role="tablist"` with a roving tabindex over the pinned and scrolling tabs;
 * arrows move focus, Enter/Space switches. The page header's filter narrows the
 * scrolling tabs; the pinned selection stays.
 */
import {
	memo,
	useCallback,
	useEffect,
	useLayoutEffect,
	useMemo,
	useRef,
	useState,
	type CSSProperties,
	type ReactNode,
} from 'react';
import { AnimatePresence, LayoutGroup, motion, useReducedMotionConfig } from 'framer-motion';
import { ChevronLeft, Search } from 'lucide-react';
import {
	AgentBadge,
	Button,
	Kbd,
	STATUS_ICON,
	STATUS_LABELS,
	STATUS_TINT,
	Tooltip,
} from '@/shared/ui';
import type { ActorStatus } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { commandChordLabel, commandChordShortcut } from '@/shared/lib/keyboard';
import { useCommandHotkey, useMediaQuery } from '@/shared/hooks';
import type { AgentEntity } from '@/modules/agents/api';
import {
	agentGist,
	badgeLabel,
	fleetCount,
	matchesAgent,
	plural,
} from '@/modules/agents/lib/stripSummary';
import { AgentHoverCard, type AgentFacts } from '@/modules/agents/components/flat/AgentHoverCard';
import { AgentPicker } from '@/modules/agents/components/flat/AgentPicker';
import { useStripLinkage } from '@/modules/agents/components/flat/useStripLinkage';
import { contentTransition, foldTransition } from '@/modules/agents/components/flat/useStickyFold';

/** Where the rail pins in the viewport: `top-0` of the shell's scroller, which
 * starts under the fixed `h-12` TopNavbar. The pinned-state observer reads it. */
const STICKY_TOP = 48;

/** Circles in the stack, desktop and phone. */
const STACK_CIRCLES = 5;
const STACK_CIRCLES_COMPACT = 2;
/** A circle (28px) and the gap after it. */
const CIRCLE_STEP = 34;
/** The `+N` / search button and the stack's end padding. */
const STACK_TAIL = 40;
/** The stack's fixed width, so the bar never shifts as circles come and go. */
function stackWidth(circles: number): number {
	return circles * CIRCLE_STEP + STACK_TAIL;
}
/** The fade each raised end casts over the tabs passing under it. */
const FADE_PX = 24;
/** The `‹ N` chip's footprint beside the pin. */
const CHIP_PX = 44;

/** framer-motion's spelling of the strip's ease-out. */
const EASE = [0.22, 1, 0.36, 1] as const;
const CIRCLE_S = 0.2;
/** A switch's one motion, shared by the tab flying into the pin, the old one
 * flying back, the rail's tabs closing and opening the gaps, and the notch: a
 * near-critically damped spring. It leaves from rest (no first-frame leap,
 * which a front-loaded ease shows whenever the switch's commit costs a frame)
 * and settles in ~300ms with no visible overshoot. */
const SWITCH_SPRING = { type: 'spring', stiffness: 380, damping: 34, mass: 0.9 } as const;
/** How long the rail counts as mid-switch: the circle linkage holds its last
 * answer until then, so circles never swap while the tabs are still moving. */
const SWITCH_SETTLE_MS = 380;
/** When the card below swaps to the new agent: once the tab has landed
 * (the spring is within a few px by then), so the new card's mount — the
 * switch's one heavy commit — never lands mid-flight. */
export const CARD_HANDOFF_MS = 260;

/** DOM id of the selected agent's panel: the one tabpanel the strip's tabs
 * switch (the pinned tab's `aria-controls` target). */
export const AGENT_PANEL_ID = 'agent-strip-panel';

/** DOM id of an agent's strip tab: what the panel is labelled by. */
export function stripTabId(agentId: string): string {
	return `agent-strip-tab-${agentId}`;
}

/**
 * The scroll offset that keeps the rail's visible tabs still across a switch:
 * `anchorBefore`/`anchorAfter` are one visible tab's `offsetLeft` before and
 * after the tab set changes (the picked tab leaving, the old pin returning,
 * the pin's width changing). Whatever moved in front of it is absorbed by the
 * scroll, clamped to the scroller's range; with no anchor, it stays put.
 */
export function compensatedScrollLeft({
	scrollLeft,
	anchorBefore,
	anchorAfter,
	maxScrollLeft,
}: {
	scrollLeft: number;
	anchorBefore: number | null;
	anchorAfter: number | null;
	maxScrollLeft: number;
}): number {
	if (anchorBefore == null || anchorAfter == null) return scrollLeft;
	// At the rail's start, it stays there: the old pin's tab lands in view
	// rather than tucked behind the pin.
	if (scrollLeft <= 0) return 0;
	const next = scrollLeft + (anchorAfter - anchorBefore);
	return Math.min(Math.max(0, next), Math.max(0, maxScrollLeft));
}

/** Non-active states whose verdict is in: their tabs read struck through.
 * `pending` is still in motion, so crossing it out would state the opposite. */
const SETTLED_STATUSES: ReadonlySet<ActorStatus> = new Set<ActorStatus>([
	'rejected',
	'disabled',
	'archived',
]);

/** How far the rail quiets each status glyph. Hues come from `STATUS_TINT`. */
const TAB_STATUS_QUIETING: Record<ActorStatus, string> = {
	// Full strength: the one state asking for a decision.
	pending: 'opacity-100',
	// Quietest of the five: it is on most tabs most of the time.
	active: 'opacity-70',
	rejected: 'opacity-80',
	disabled: 'opacity-80',
	archived: 'opacity-60',
};

/** The raised ends' lift onto the row, while something passes under them. */
const LIFT_LEFT = 'shadow-[10px_0_16px_-12px_rgb(0_0_0/0.7)]';
const LIFT_RIGHT = 'shadow-[-10px_0_16px_-12px_rgb(0_0_0/0.7)]';

const layoutIdOf = (id: string) => `agent-strip-tab-${id}`;

interface StripTabProps {
	agent: AgentEntity;
	/** Counts as primitives, so the memo holds while the rail scrolls. */
	apiCount: number | undefined;
	credentialCount: number | undefined;
	blocked: number | undefined;
	gaps: number | undefined;
	/** The selected agent's tab, in the pin. */
	pinned: boolean;
	/** Phone width: the pinned tab drops to avatar + a short name. */
	compact: boolean;
	focusable: boolean;
	reduced: boolean;
	registerRef: (id: string, el: HTMLButtonElement | null) => void;
	onActivate: (id: string) => void;
}

/** One tab, in the pin or the rail, with its hover card. */
const StripTab = memo(function StripTab({
	agent,
	apiCount,
	credentialCount,
	blocked,
	gaps = 0,
	pinned,
	compact,
	focusable,
	reduced,
	registerRef,
	onActivate,
}: StripTabProps) {
	const isPending = agent.status === 'pending';
	const isSettled = SETTLED_STATUSES.has(agent.status);
	const StatusIcon = STATUS_ICON[agent.status];
	const facts: AgentFacts = { apiCount, credentialCount, blocked, gaps };
	const slim = pinned && compact;
	return (
		<Tooltip
			content={<AgentHoverCard agent={agent} facts={facts} selected={pinned} />}
			interactiveChild
			placement="bottom"
			delayMs={250}
			className="shrink-0"
			bubbleClassName="bg-surface-sheet/95"
		>
			<motion.button
				ref={(el: HTMLButtonElement | null) => registerRef(agent.id, el)}
				layoutId={layoutIdOf(agent.id)}
				// Position only: the pin and the rail draw a tab at much the same
				// width, and a size tween would stretch the label in between.
				layout="position"
				transition={{ layout: reduced ? { duration: 0 } : SWITCH_SPRING }}
				type="button"
				role="tab"
				id={stripTabId(agent.id)}
				// One panel shows the selection, so only the pinned tab controls it.
				aria-controls={pinned ? AGENT_PANEL_ID : undefined}
				// The rail's tabs are what the linkage observer watches; the pin is not.
				data-strip-tab={pinned ? undefined : agent.id}
				data-agent-id={agent.id}
				data-pinned={pinned || undefined}
				aria-selected={pinned}
				tabIndex={focusable ? 0 : -1}
				onClick={() => onActivate(agent.id)}
				className={cn(
					'group/tab relative flex h-9 shrink-0 items-center gap-2 rounded-[9px] pr-3 pl-1.5 text-[13.5px] font-semibold whitespace-nowrap transition-colors duration-150',
					'focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none',
					pinned
						? 'bg-surface-tonal text-foreground-name shadow-[inset_0_1px_0_hsl(0_0%_100%/0.04)]'
						: // The `sub` tier, so the pinned wash registers against the idle tabs.
							'text-foreground-sub hover:text-foreground-name hover:bg-tint-2',
					slim && 'gap-1.5 pr-2.5',
				)}
			>
				<AgentBadge id={agent.id} name={agent.name} size="xs" shape="circle" />
				{/* Every state gets a glyph, active included: the shape tells the five apart. */}
				{!slim && (
					<StatusIcon
						aria-hidden="true"
						className={cn(
							'size-3.5 shrink-0',
							STATUS_TINT[agent.status],
							TAB_STATUS_QUIETING[agent.status],
						)}
					/>
				)}
				{/* Struck through when the name will not serve and won't change on its own.
				    `dir="auto"` isolates the operator-chosen name so an override inside
				    it cannot reverse the count beside it (#1543). */}
				<span
					dir="auto"
					className={cn(
						'min-w-0 truncate',
						slim ? 'max-w-[8ch]' : 'max-w-[18ch] max-md:max-w-[11ch]',
						isSettled && 'line-through decoration-from-font',
					)}
				>
					{agent.name}
				</span>
				{isPending && <span className="sr-only">(awaiting approval)</span>}
				{isSettled && <span className="sr-only">({STATUS_LABELS[agent.status]})</span>}
				{apiCount != null && !isPending && !slim && (
					<span
						data-testid="strip-api-count"
						className={cn(
							'grid h-[18px] min-w-[18px] place-items-center rounded-full px-[5px] text-[11px] font-bold tabular-nums',
							pinned
								? 'bg-primary/15 text-primary'
								: 'bg-surface-chip text-foreground-faint',
						)}
					>
						{/* The bare digit is visual; the tab's name says what it counts. */}
						<span aria-hidden="true">{apiCount}</span>
						<span className="sr-only">{badgeLabel(apiCount, credentialCount)}</span>
					</span>
				)}
				{gaps > 0 && !slim && (
					<span className="text-foreground-sub text-xs font-medium">
						· {gaps} to set up
					</span>
				)}
			</motion.button>
		</Tooltip>
	);
});

/** A linked circle: the agent's avatar, switching to it on click. */
function StackCircle({
	agent,
	facts,
	onActivate,
}: {
	agent: AgentEntity;
	facts: AgentFacts;
	onActivate: (id: string) => void;
}) {
	const gist = agentGist(agent, facts.apiCount);
	return (
		<Tooltip
			content={<AgentHoverCard agent={agent} facts={facts} selected={false} />}
			interactiveChild
			placement="bottom"
			delayMs={250}
			bubbleClassName="bg-surface-sheet/95"
		>
			<button
				type="button"
				data-testid="strip-circle"
				data-agent-id={agent.id}
				aria-label={`${agent.name}${gist ? `, ${gist}` : ''} — switch to it`}
				onClick={() => onActivate(agent.id)}
				className={cn(
					'relative grid size-7 shrink-0 place-items-center rounded-full transition-transform duration-150 hover:-translate-y-px motion-reduce:hover:translate-y-0',
					'focus-visible:ring-primary/70 focus-visible:ring-offset-surface-1 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none',
				)}
			>
				<AgentBadge
					id={agent.id}
					name={agent.name}
					size="sm"
					shape="circle"
					dimmed={agent.status === 'archived'}
				/>
				{agent.status === 'pending' && (
					<span
						aria-hidden="true"
						className="bg-warning ring-surface-1 absolute -top-px -right-px size-2 rounded-full ring-2"
					/>
				)}
			</button>
		</Tooltip>
	);
}

interface AgentStripProps {
	agents: AgentEntity[];
	selectedId: string | null;
	onSelect: (id: string) => void;
	/** Per-agent count of bound credentials that are not usable yet. */
	setupGaps: ReadonlyMap<string, number>;
	/** Per-agent count of reachable APIs. A missing key shows no count rather
	 * than a figure the join can't prove. */
	apiCounts: ReadonlyMap<string, number>;
	/** Per-agent count of bound credentials, for the hover card. */
	credentialCounts?: ReadonlyMap<string, number>;
	/** Per-agent count of Blocked rows — known only for an agent whose rules
	 * were read (the selected one); absent agents claim nothing. */
	blockedCounts?: ReadonlyMap<string, number>;
	/** The page header's filter text (this component consumes it, not owns it). */
	filter: string;
	/** Reports the bar's height: where the agent card pins, under it — and its
	 * height condensed (less the header row), where the pinned card sits once
	 * the strip condenses over it. Both measured, never assumed. */
	onHeight?: (px: number, condensedPx: number) => void;
	/** The agent card is pinned under the pinned bar, so the notch points into
	 * it again. */
	cardPinned?: boolean;
	/** The card is stuck under the bar: the header row slides away (the bar
	 * rides up by its height) to give the pinned stack the room. */
	condensed?: boolean;
	/** The picker's open state, when a parent owns it (so a control outside
	 * the strip — the card's name — opens the same picker). Uncontrolled
	 * without it. */
	pickerOpen?: boolean;
	onPickerOpenChange?: (open: boolean) => void;
	/** `agents` is not the whole fleet (pages still to come, or a later one
	 * failed): every fleet total reads as a floor ("38+", "at least 38"). */
	incomplete?: boolean;
}

export function AgentStrip({
	agents,
	selectedId,
	onSelect,
	setupGaps,
	apiCounts,
	credentialCounts,
	blockedCounts,
	filter,
	onHeight,
	cardPinned = false,
	condensed = false,
	pickerOpen: pickerOpenProp,
	onPickerOpenChange,
	incomplete = false,
}: AgentStripProps) {
	const reduced = useReducedMotionConfig() ?? false;
	const compact = useMediaQuery('(max-width: 767px)');
	const circleCount = compact ? STACK_CIRCLES_COMPACT : STACK_CIRCLES;
	const stackPx = stackWidth(circleCount);

	const barRef = useRef<HTMLDivElement | null>(null);
	const railRef = useRef<HTMLDivElement | null>(null);
	const scrollerRef = useRef<HTMLDivElement | null>(null);
	const pinRef = useRef<HTMLDivElement | null>(null);
	const tabRefs = useRef(new Map<string, HTMLButtonElement>());
	const registerRef = useCallback((id: string, el: HTMLButtonElement | null) => {
		if (el) tabRefs.current.set(id, el);
		else tabRefs.current.delete(id);
	}, []);

	const byId = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
	const selected = selectedId ? (byId.get(selectedId) ?? null) : null;
	// The picker's fleet, waiting first (re-asserted, whatever order arrives).
	const fleet = useMemo(
		() => [
			...agents.filter((a) => a.status === 'pending'),
			...agents.filter((a) => a.status !== 'pending'),
		],
		[agents],
	);
	const waitingCount = useMemo(
		() => agents.filter((a) => a.status === 'pending').length,
		[agents],
	);

	// The scroll list: the filter's matches less the pinned selection. Pending
	// tabs lead, so DOM order and arrow-key traversal agree with the groups.
	const list = useMemo(() => {
		const matched = agents.filter((a) => a.id !== selectedId && matchesAgent(a, filter));
		return {
			pending: matched.filter((a) => a.status === 'pending'),
			rest: matched.filter((a) => a.status !== 'pending'),
		};
	}, [agents, filter, selectedId]);
	const order = useMemo(() => [...list.pending, ...list.rest].map((a) => a.id), [list]);
	const nothingMatches = order.length === 0 && filter.trim() !== '';
	// Roving order: the pin, then the rail.
	const keyOrder = useMemo(() => (selected ? [selected.id, ...order] : order), [selected, order]);

	const factsOf = useCallback(
		(id: string): AgentFacts => ({
			apiCount: apiCounts.get(id),
			credentialCount: credentialCounts?.get(id),
			blocked: blockedCounts?.get(id),
			gaps: setupGaps.get(id),
		}),
		[apiCounts, credentialCounts, blockedCounts, setupGaps],
	);

	// Mid-switch: from the render that moves the selection until the tabs have
	// settled. Derived in render, so the switch's own commit already knows.
	const [settledId, setSettledId] = useState(selectedId);
	const switching = !reduced && settledId !== selectedId;
	useEffect(() => {
		if (settledId === selectedId) return;
		const t = window.setTimeout(() => setSettledId(selectedId), reduced ? 0 : SWITCH_SETTLE_MS);
		return () => window.clearTimeout(t);
	}, [settledId, selectedId, reduced]);

	// Scroll anchoring across a switch: the first tab showing past the pin is
	// read just before the switch and held where it was just after, so the tabs
	// in view don't lurch as the picked one leaves and the old pin returns
	// somewhere before them. Only the gap the picked tab leaves closes.
	const anchorRef = useRef<{ id: string; x: number } | null>(null);
	const contentX = (sc: HTMLElement, el: HTMLElement) =>
		el.getBoundingClientRect().left - sc.getBoundingClientRect().left + sc.scrollLeft;
	const captureAnchor = useCallback((pickedId: string) => {
		anchorRef.current = null;
		const sc = scrollerRef.current;
		const pin = pinRef.current;
		if (!sc) return;
		const start = (pin?.getBoundingClientRect().right ?? sc.getBoundingClientRect().left) + 1;
		for (const el of sc.querySelectorAll<HTMLElement>('[data-strip-tab]')) {
			const id = el.dataset.stripTab;
			if (!id || id === pickedId) continue;
			if (el.getBoundingClientRect().right > start) {
				anchorRef.current = { id, x: contentX(sc, el) };
				return;
			}
		}
	}, []);
	useLayoutEffect(() => {
		const anchor = anchorRef.current;
		anchorRef.current = null;
		const sc = scrollerRef.current;
		if (!anchor || !sc) return;
		const el = sc.querySelector<HTMLElement>(`[data-strip-tab="${CSS.escape(anchor.id)}"]`);
		sc.scrollLeft = compensatedScrollLeft({
			scrollLeft: sc.scrollLeft,
			anchorBefore: anchor.x,
			anchorAfter: el ? contentX(sc, el) : null,
			maxScrollLeft: sc.scrollWidth - sc.clientWidth,
		});
	}, [selectedId]);

	// The pin's width insets the rail and the observer; re-measured as fonts,
	// counts or the selection change it.
	const [pinPx, setPinPx] = useState(0);
	useLayoutEffect(() => {
		const pin = pinRef.current;
		if (!pin) {
			setPinPx(0);
			return;
		}
		setPinPx(pin.offsetWidth);
		if (typeof ResizeObserver === 'undefined') return;
		const obs = new ResizeObserver(() => setPinPx(pin.offsetWidth));
		obs.observe(pin);
		return () => obs.disconnect();
	}, [selectedId]);

	const linkage = useStripLinkage({
		scrollerRef,
		order,
		startInset: pinPx,
		endInset: stackPx,
		paused: switching,
	});
	const inRail = useMemo(() => new Set(order), [order]);
	const offRight = useMemo(() => linkage.right.filter((id) => inRail.has(id)), [linkage, inRail]);
	const circles = offRight.slice(0, circleCount);
	const more = offRight.length - circles.length;
	const offLeft = linkage.left;
	const chipShown = offLeft > 0 && !compact;

	// "Something is behind the pin": a per-frame attribute, not React state, so
	// scrolling re-renders nothing. CSS keys the pin's fade and lift off it.
	useEffect(() => {
		const sc = scrollerRef.current;
		const rail = railRef.current;
		if (!sc || !rail) return;
		let raf = 0;
		const update = () => {
			raf = 0;
			rail.dataset.railScrolled = String(sc.scrollLeft > 1);
		};
		const onScroll = () => {
			if (!raf) raf = requestAnimationFrame(update);
		};
		update();
		sc.addEventListener('scroll', onScroll, { passive: true });
		return () => {
			sc.removeEventListener('scroll', onScroll);
			if (raf) cancelAnimationFrame(raf);
		};
	}, []);

	// Hairline once the bar is actually pinned: it watches ITSELF against a root
	// inset one pixel past the sticky offset, which clips it only when pinned.
	useEffect(() => {
		const bar = barRef.current;
		if (!bar || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			([entry]) => {
				bar.dataset.scrolled = entry?.isIntersecting ? 'false' : 'true';
			},
			{ threshold: [1], rootMargin: `-${STICKY_TOP + 1}px 0px 0px 0px` },
		);
		obs.observe(bar);
		return () => obs.disconnect();
	}, []);

	// The bar's height, for the card that pins under it, and the header row's,
	// which the bar sheds when it condenses. Layout heights: the condensing
	// transform never changes them, so this never feeds back into the pin.
	const headerRef = useRef<HTMLDivElement | null>(null);
	useEffect(() => {
		const bar = barRef.current;
		if (!bar || !onHeight) return;
		const report = () => {
			// Sub-pixel exact (not `offsetHeight`'s rounding), so the pinned card
			// meets the bar's bottom edge rather than tucking a fraction under it.
			// Transforms don't change a rect's height.
			const full = bar.getBoundingClientRect().height;
			onHeight(full, full - (headerRef.current?.getBoundingClientRect().height ?? 0));
		};
		report();
		if (typeof ResizeObserver === 'undefined') return;
		const obs = new ResizeObserver(report);
		obs.observe(bar);
		if (headerRef.current) obs.observe(headerRef.current);
		return () => obs.disconnect();
	}, [onHeight]);
	const [headerPx, setHeaderPx] = useState(0);
	useLayoutEffect(() => {
		const head = headerRef.current;
		if (!head) return;
		const read = () => setHeaderPx(head.getBoundingClientRect().height);
		read();
		if (typeof ResizeObserver === 'undefined') return;
		const obs = new ResizeObserver(read);
		obs.observe(head);
		return () => obs.disconnect();
	}, []);

	// Roving focus: one tab is in the Tab order — the one the arrows last
	// reached, else the pinned selection, else the first in the rail.
	const [focusId, setFocusId] = useState<string | null>(null);
	const focusableId = focusId && keyOrder.includes(focusId) ? focusId : (keyOrder[0] ?? null);

	// A keyboard switch made from inside the bar moves focus to the pinned tab,
	// since the tab that was focused has just left the rail. A pointer switch
	// does not: focusing the pin would pop its hover card over the new card.
	const refocusPin = useRef(false);
	// A pick made from the card's name hands focus to the new card's name (the
	// old one leaves with its card): holds the old name's element until then.
	const refocusSwitcher = useRef<HTMLElement | null>(null);
	// The selection as of the last render: read by the stable handlers below,
	// and at the picker's close time (a pick has just moved it).
	const selectedRef = useRef(selectedId);
	selectedRef.current = selectedId;
	const onActivate = useCallback(
		(id: string) => {
			const active = document.activeElement;
			if (id === selectedRef.current) return;
			// Only a switch that happens moves focus to the pin: set after the
			// already-selected check, or a no-op press would leave it armed for
			// the next selection change made elsewhere.
			refocusPin.current =
				active instanceof HTMLElement &&
				(railRef.current?.contains(active) ?? false) &&
				active.matches(':focus-visible');
			captureAnchor(id);
			onSelect(id);
		},
		// Stable across switches (the selection is read from a ref), so the
		// memoised tabs don't all re-render — and rebuild their hover cards —
		// in the switch's own commit.
		[onSelect, captureAnchor],
	);
	useLayoutEffect(() => {
		setFocusId(null);
		const oldSwitcher = refocusSwitcher.current;
		if (oldSwitcher) {
			refocusSwitcher.current = null;
			// The new card's name: a new element, mounted in this commit or —
			// while the strip's tab is still flying — a beat later, once the
			// card hands off. The closing dialog also hands focus back a frame
			// after this, so keep taking it until the new name holds it.
			const until = performance.now() + CARD_HANDOFF_MS + 700;
			let raf = 0;
			let settled = 0;
			const focusSwitcher = () => {
				const el = document.querySelector<HTMLElement>('[data-agent-switcher]');
				if (el && el !== oldSwitcher) {
					if (document.activeElement !== el) el.focus({ preventScroll: true });
					if (++settled > 1) return;
				}
				if (performance.now() < until) raf = requestAnimationFrame(focusSwitcher);
			};
			focusSwitcher();
			return () => cancelAnimationFrame(raf);
		}
		if (!refocusPin.current || !selectedId) return;
		refocusPin.current = false;
		const id = selectedId;
		tabRefs.current.get(id)?.focus({ preventScroll: true });
		// A closing picker dialog hands focus back on its own a beat later;
		// take it once more after that.
		const raf = requestAnimationFrame(() => {
			const tab = tabRefs.current.get(id);
			if (tab && document.activeElement !== tab) tab.focus({ preventScroll: true });
		});
		return () => cancelAnimationFrame(raf);
	}, [selectedId]);

	/** Scroll a rail tab clear of both raised ends. */
	function revealTab(el: HTMLElement) {
		const sc = scrollerRef.current;
		if (!sc || el.closest('[data-strip-pin]')) return;
		const box = sc.getBoundingClientRect();
		const tab = el.getBoundingClientRect();
		const start = box.left + pinPx + (chipShown ? CHIP_PX : 0) + FADE_PX;
		const end = box.right - stackPx - FADE_PX / 2;
		const dx = tab.left < start ? tab.left - start : tab.right > end ? tab.right - end : 0;
		if (dx !== 0) sc.scrollBy({ left: dx, behavior: reduced ? 'auto' : 'smooth' });
	}

	/** Arrows/Home/End move focus over the pin and the rail; Enter/Space switch. */
	function handleTablistKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
		const id = (e.target as HTMLElement)
			.closest('[data-agent-id]')
			?.getAttribute('data-agent-id');
		const at = id ? keyOrder.indexOf(id) : -1;
		if (at < 0 || keyOrder.length === 0) return;
		const last = keyOrder.length - 1;
		const next =
			e.key === 'ArrowRight'
				? (at + 1) % keyOrder.length
				: e.key === 'ArrowLeft'
					? (at - 1 + keyOrder.length) % keyOrder.length
					: e.key === 'Home'
						? 0
						: e.key === 'End'
							? last
							: null;
		if (next == null) return;
		e.preventDefault();
		const nextId = keyOrder[next];
		setFocusId(nextId);
		const el = tabRefs.current.get(nextId);
		if (!el) return;
		el.focus({ preventScroll: true });
		revealTab(el);
	}

	/** `‹ N`: page back toward the start of the rail. */
	function pageBack() {
		const sc = scrollerRef.current;
		if (!sc) return;
		const page = sc.clientWidth - pinPx - stackPx - 40;
		sc.scrollBy({ left: -Math.max(120, page), behavior: reduced ? 'auto' : 'smooth' });
	}

	// The picker: ⌘K / Ctrl+K toggles it (also from a text field), `+N` and the
	// end-state search button open it — and, with the state lifted to the
	// parent, the card's name too. Closing hands focus back to the opener: the
	// control focused when it opened, read whichever way it was opened.
	const [ownPickerOpen, setOwnPickerOpen] = useState(false);
	const pickerControlled = pickerOpenProp !== undefined;
	const pickerOpen = pickerControlled ? pickerOpenProp : ownPickerOpen;
	const setPickerOpen = useCallback(
		(open: boolean) => {
			if (!pickerControlled) setOwnPickerOpen(open);
			onPickerOpenChange?.(open);
		},
		[pickerControlled, onPickerOpenChange],
	);
	const openerRef = useRef<HTMLElement | null>(null);
	// Opened from outside (the parent flipped `pickerOpen`): the opener is
	// whatever held focus as it opened. A layout effect, so it reads focus
	// before the dialog's own (passive) effect moves it in.
	useLayoutEffect(() => {
		if (pickerOpen && !openerRef.current) {
			openerRef.current =
				document.activeElement instanceof HTMLElement ? document.activeElement : null;
		}
	}, [pickerOpen]);
	const openPicker = useCallback(() => {
		openerRef.current =
			document.activeElement instanceof HTMLElement ? document.activeElement : null;
		setPickerOpen(true);
	}, [setPickerOpen]);
	const pickedRef = useRef<string | null>(null);
	const pickAgent = useCallback(
		(id: string) => {
			pickedRef.current = id;
			if (id !== selectedRef.current) captureAnchor(id);
			onSelect(id);
		},
		[onSelect, captureAnchor],
	);
	const closePicker = useCallback(() => {
		setPickerOpen(false);
		const opener = openerRef.current;
		const picked = pickedRef.current;
		openerRef.current = null;
		pickedRef.current = null;
		// A pick lands focus on its tab in the pin, once the selection arrives
		// (the tab the picker was opened from may be the one leaving the pin) —
		// or, opened from the card's name, on the new card's name.
		if (picked && picked !== selectedRef.current) {
			if (opener?.matches('[data-agent-switcher]')) refocusSwitcher.current = opener;
			else refocusPin.current = true;
			return;
		}
		// Dismissed: focus goes back where the picker was opened from.
		requestAnimationFrame(() => {
			const current = selectedRef.current;
			const target =
				opener?.isConnected && opener !== document.body
					? opener
					: current
						? tabRefs.current.get(current)
						: null;
			target?.focus({ preventScroll: true });
		});
	}, [setPickerOpen]);
	useCommandHotkey('k', () => (pickerOpen ? closePicker() : openPicker()), {
		whileOverlay: pickerOpen,
	});

	const kbd = commandChordLabel('k');
	const circleTransition = { duration: reduced ? 0 : CIRCLE_S, ease: EASE };

	function renderTab(agent: AgentEntity, pinned: boolean) {
		return (
			<StripTab
				key={agent.id}
				agent={agent}
				apiCount={apiCounts.get(agent.id)}
				credentialCount={credentialCounts?.get(agent.id)}
				blocked={blockedCounts?.get(agent.id)}
				gaps={setupGaps.get(agent.id)}
				pinned={pinned}
				compact={compact}
				focusable={agent.id === focusableId}
				reduced={reduced}
				registerRef={registerRef}
				onActivate={onActivate}
			/>
		);
	}

	const railStyle = {
		'--strip-pin-w': `${pinPx}px`,
		'--strip-stack-w': `${stackPx}px`,
	} as CSSProperties;
	// Condensed over the stuck card: the bar rides up by the header row's
	// height (the scroller clips what goes above its top), on the fold's curve,
	// so the rail keeps its own top padding and the notch moves with the card.
	const condense = condensed && headerPx > 0;
	const barStyle: CSSProperties = {
		transform: condense ? `translateY(-${headerPx}px)` : undefined,
		transition: [
			'box-shadow 150ms',
			'border-color 150ms',
			foldTransition('transform', condense, reduced),
		]
			.filter((t) => t !== 'none')
			.join(', '),
	};

	return (
		// Initials come from the fleet-wide AgentInitialsProvider the parent
		// (FlatAgentsSection) already wraps this strip in — don't recompute them.
		<>
			<div
				ref={barRef}
				data-scrolled="false"
				data-card-pinned={cardPinned}
				data-condensed={condense || undefined}
				data-testid="agent-strip"
				style={barStyle}
				// Bleeds to the gutter edges so the backdrop covers the tiles passing under.
				// The border is transparent until it sticks, so pinning costs no layout shift.
				className="-mx-page-gutter px-page-gutter bg-background/85 data-[scrolled=true]:border-hairline group/strip data-[card-pinned=true]:bg-background sticky top-0 z-20 border-b border-transparent pt-2 pb-2.5 backdrop-blur data-[card-pinned=true]:border-transparent! data-[card-pinned=true]:shadow-none! data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
			>
				<StripHeader
					ref={headerRef}
					total={agents.length}
					incomplete={incomplete}
					waiting={waitingCount}
					kbd={kbd}
					condensed={condense}
					reduced={reduced}
				/>
				<div
					ref={railRef}
					data-testid="strip-rail"
					data-rail-scrolled="false"
					style={railStyle}
					className="group/rail bg-surface-1 relative flex min-w-0 rounded-xl"
				>
					<LayoutGroup id="agent-strip">
						<div
							role="tablist"
							aria-label="Agents"
							onKeyDown={handleTablistKeyDown}
							className="flex min-w-0 flex-1"
						>
							<motion.div
								ref={scrollerRef}
								// Framer reads the rail's scroll offset into every tab's box,
								// so a switch made scrolled starts each flight from where the
								// tab really was.
								layoutScroll
								role="presentation"
								data-testid="strip-scroller"
								style={{
									paddingLeft: pinPx + 6,
									paddingRight: stackPx,
									scrollPaddingLeft: pinPx + FADE_PX + 6,
									scrollPaddingRight: stackPx + 6,
								}}
								className={cn(
									'flex min-h-11 min-w-0 flex-1 items-center gap-0.5 overflow-x-auto overflow-y-hidden overscroll-x-contain py-1',
									// One row at every width — the rail scrolls, it never wraps.
									'[scrollbar-width:none] flex-nowrap [&::-webkit-scrollbar]:hidden',
								)}
							>
								{/* The pin sits in the scroller's DOM (so the roving tab stop is
								    inside the scroll region) but its containing block is the
								    rail: absolutely positioned against it, it neither scrolls
								    nor clips with the tabs. */}
								{selected && (
									<div
										ref={pinRef}
										data-strip-pin=""
										data-testid="strip-pin"
										className={cn(
											'bg-surface-1 absolute inset-y-0 left-0 z-[3] flex items-center rounded-l-xl p-1 transition-shadow duration-200 motion-reduce:transition-none',
											// The fade + lift onto the tabs behind it; the `‹ N`
											// chip takes both over while it shows.
											!chipShown &&
												'after:from-surface-1 group-data-[rail-scrolled=true]/rail:shadow-[10px_0_16px_-12px_rgb(0_0_0/0.7)] after:pointer-events-none after:absolute after:inset-y-0 after:left-full after:w-6 after:bg-linear-to-r after:to-transparent after:opacity-0 after:transition-opacity after:duration-200 group-data-[rail-scrolled=true]/rail:after:opacity-100',
										)}
									>
										{renderTab(selected, true)}
										{/* The notch: a square in the card's tone, turned 45° and half
										    inside the card's top edge, under the pinned tab — so it never
										    moves with the rail. Hidden once the bar pins, unless the
										    card has pinned under it, so it is beneath it again. */}
										<motion.span
											aria-hidden="true"
											data-testid="strip-notch"
											data-card-pinned={cardPinned}
											// Slides with the pin's width change instead of popping.
											layout="position"
											transition={{
												layout: reduced ? { duration: 0 } : SWITCH_SPRING,
											}}
											style={{ rotate: 45 }}
											className="bg-surface-1 border-hairline-field pointer-events-none absolute top-[calc(100%+4px)] left-1/2 -ml-[6.5px] h-[13px] w-[13px] border-t border-l group-data-[scrolled=true]/strip:data-[card-pinned=false]:hidden"
										/>
									</div>
								)}
								{list.pending.length > 0 && (
									<>
										{/* Visual only — the sr-only tab suffix carries the state. */}
										<motion.span
											aria-hidden="true"
											data-testid="strip-pending-label"
											layout="position"
											transition={{
												layout: reduced ? { duration: 0 } : SWITCH_SPRING,
											}}
											className="text-warning flex shrink-0 items-center gap-1.5 px-1.5 text-xs font-medium"
										>
											<span className="bg-warning h-1.5 w-1.5 animate-pulse rounded-full motion-reduce:animate-none" />
											Waiting
										</motion.span>
										{list.pending.map((a) => renderTab(a, false))}
										{list.rest.length > 0 && (
											<motion.span
												aria-hidden="true"
												data-testid="strip-pending-divider"
												layout="position"
												transition={{
													layout: reduced
														? { duration: 0 }
														: SWITCH_SPRING,
												}}
												className="bg-hairline-field mx-1.5 h-5 w-px shrink-0 self-center"
											/>
										)}
									</>
								)}
								{list.rest.map((a) => renderTab(a, false))}
							</motion.div>
						</div>
					</LayoutGroup>
					{/* Outside the tablist (it owns tabs only), on the rail's raised layer. */}
					{nothingMatches && (
						<p
							role="status"
							className="text-foreground-sub pointer-events-none absolute inset-y-0 flex items-center text-sm"
							style={{ left: pinPx + 12 }}
						>
							No agents match your filter.
						</p>
					)}
					{chipShown && (
						<Button
							variant="ghost"
							size="icon"
							data-testid="strip-left-chip"
							onClick={pageBack}
							aria-label={`${plural(offLeft, 'earlier agent')} — scroll back`}
							style={{ left: pinPx }}
							className={cn(
								'group/chip bg-surface-1 hover:bg-surface-1 absolute inset-y-0 z-[3] flex h-auto rounded-none p-0 px-0.5 font-normal active:scale-100',
								'focus-visible:ring-0 focus-visible:ring-offset-0 focus-visible:outline-none',
								'after:from-surface-1 after:pointer-events-none after:absolute after:inset-y-0 after:left-full after:w-6 after:bg-linear-to-r after:to-transparent',
								LIFT_LEFT,
							)}
						>
							<span className="bg-surface-chip text-foreground-sub group-hover/chip:bg-surface-tonal-hover group-hover/chip:text-foreground group-focus-visible/chip:ring-primary/60 inline-flex h-5 items-center gap-px rounded-full pr-[7px] pl-1 text-[10.5px] font-bold tabular-nums transition-colors group-focus-visible/chip:ring-2">
								<ChevronLeft aria-hidden="true" className="size-3" />
								{offLeft}
							</span>
						</Button>
					)}
					<div
						data-testid="strip-stack"
						style={{ width: stackPx }}
						className={cn(
							'bg-surface-1 absolute inset-y-0 right-0 z-[3] flex items-center justify-end gap-1.5 rounded-r-xl pr-1.5 transition-shadow duration-200 motion-reduce:transition-none',
							offRight.length > 0 &&
								cn(
									'before:from-surface-1 before:pointer-events-none before:absolute before:inset-y-0 before:right-full before:w-6 before:bg-linear-to-l before:to-transparent',
									LIFT_RIGHT,
								),
						)}
					>
						<AnimatePresence mode="popLayout" initial={false}>
							{circles.map((id) => {
								const agent = byId.get(id);
								if (!agent) return null;
								return (
									<motion.div
										key={id}
										layout={reduced ? false : 'position'}
										initial={{ opacity: 0, x: 10, scale: 0.6 }}
										animate={{ opacity: 1, x: 0, scale: 1 }}
										exit={{ opacity: 0, x: -14, scale: 0.6 }}
										transition={circleTransition}
										className="flex shrink-0"
									>
										<StackCircle
											agent={agent}
											facts={factsOf(id)}
											onActivate={onActivate}
										/>
									</motion.div>
								);
							})}
						</AnimatePresence>
						<MoreButton
							more={more}
							total={agents.length}
							incomplete={incomplete}
							kbd={kbd}
							onOpen={openPicker}
						/>
					</div>
				</div>
			</div>
			<AgentPicker
				open={pickerOpen}
				onClose={closePicker}
				agents={fleet}
				selectedId={selectedId}
				onSelect={pickAgent}
				apiCounts={apiCounts}
				incomplete={incomplete}
			/>
		</>
	);
}

/** "Agents 38 · 2 waiting" on the left, the picker's shortcut on the right.
 * Condensed, it fades out as the bar rides up over it (the `+N` / search
 * button keeps the shortcut, in its tooltip and `aria-keyshortcuts`). */
function StripHeader({
	ref,
	total,
	incomplete,
	waiting,
	kbd,
	condensed,
	reduced,
}: {
	ref: React.Ref<HTMLDivElement>;
	total: number;
	incomplete: boolean;
	waiting: number;
	kbd: string;
	condensed: boolean;
	reduced: boolean;
}) {
	return (
		<div
			ref={ref}
			data-testid="strip-header"
			aria-hidden={condensed || undefined}
			style={{
				opacity: condensed ? 0 : 1,
				transition: contentTransition(['opacity'], condensed, reduced),
			}}
			className="flex items-center justify-between gap-3 pb-1.5 pl-1 text-xs font-semibold"
		>
			<p className="text-foreground-sub flex items-center gap-1.5">
				<span className="text-foreground-name">Agents</span>
				<span className="tabular-nums">{fleetCount(total, incomplete)}</span>
				{waiting > 0 && (
					<>
						<span aria-hidden="true">·</span>
						<span className="text-warning inline-flex items-center gap-1.5">
							<span aria-hidden="true" className="bg-warning size-1.5 rounded-full" />
							{waiting} waiting
						</span>
					</>
				)}
			</p>
			{/* A hint, not a control: `+N` and the search button are the clickable ways in. */}
			<p className="text-foreground-faint hidden items-center gap-1.5 pr-1.5 font-medium md:flex">
				Switch agent <Kbd>{kbd}</Kbd>
			</p>
		</div>
	);
}

/** `+N` (the exact rest off to the right) or, with none, the search button. */
function MoreButton({
	more,
	total,
	incomplete,
	kbd,
	onOpen,
}: {
	more: number;
	total: number;
	incomplete: boolean;
	kbd: string;
	onOpen: () => void;
}): ReactNode {
	// With the fleet still loading (or a page failed), the total is a floor.
	const fleet = incomplete ? `at least ${total} agents` : `all ${total} agents`;
	const Fleet = fleet[0].toUpperCase() + fleet.slice(1);
	const label =
		more > 0
			? `${more} more ${more === 1 ? 'agent' : 'agents'} further right — open the agent picker`
			: `${Fleet} — open the agent picker`;
	const tip = more > 0 ? `${more} more · search ${fleet} (${kbd})` : `${Fleet} (${kbd})`;
	return (
		<Tooltip content={tip} interactiveChild placement="bottom" delayMs={250}>
			<Button
				variant="ghost"
				size="icon-xs"
				data-testid="strip-more"
				data-end={more > 0 ? undefined : ''}
				aria-label={label}
				aria-haspopup="dialog"
				aria-keyshortcuts={commandChordShortcut('k')}
				onClick={onOpen}
				className={cn(
					'bg-surface-chip text-foreground-sub hover:bg-surface-tonal-hover hover:text-foreground w-auto min-w-7 shrink-0 rounded-full px-[7px] text-[10.5px] font-bold tracking-[-0.02em] tabular-nums transition-colors active:scale-100',
					'focus-visible:ring-primary/70 focus-visible:ring-offset-surface-1',
				)}
			>
				{more > 0 ? `+${more}` : <Search aria-hidden="true" className="size-3.5" />}
			</Button>
		</Tooltip>
	);
}
