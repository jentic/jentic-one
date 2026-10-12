/**
 * useHoverIntent — open-on-purpose hover for a list of rows that grow when
 * pointed at. A plain `:hover`/pointerenter opens every row the cursor
 * crosses, and every row that SLIDES under a resting cursor while the list
 * scrolls; this opens a row only when a real pointer has come to rest on it.
 *
 *  - Opens after the pointer, having MOVED onto the element, rests on it for
 *    `openDelayMs` (moving restarts the wait), so a pass over the list opens
 *    nothing — and neither does a row that arrives under a still cursor
 *    (the list scrolled, or a row above it folded): only real movement arms.
 *  - Scrolling suppresses it: from a wheel or scroll on the element's scroll
 *    container until `scrollQuietMs` after the last one AND the pointer has
 *    actually moved — content moving under a still cursor never opens a row.
 *  - Leaving closes after `closeGraceMs`; coming back in that window keeps it
 *    open, so crossing the gap between rows doesn't flicker.
 *  - Row to row: a row left for a sibling that is about to open stays open
 *    until that sibling opens, then folds in the same frame — one row grows as
 *    the other shrinks, so the list's height holds and the row under the
 *    pointer keeps it (siblings share the scroll container).
 *  - While rows grow or fold (`layoutSettleMs` after any change), a leave is
 *    not taken at its word: the browser also reports one when the layout
 *    moves the row out from under a still pointer. It is checked against the
 *    pointer's real position at its next real movement (or once the layout
 *    has settled) — only a pointer that really left counts.
 *  - Pinning is the caller's (`pinned`, typically from `usePinStack`, so
 *    several siblings can be pinned at once). A pinned row is open for the
 *    caller's reasons, so hover leaves it entirely alone: it never arms, a
 *    leave never folds it, and it takes no part in a handoff. `open` is the
 *    hover PREVIEW only — it drops when the row is pinned (the pin holds it
 *    open in the same frame) and siblings left for this one fold with it. A
 *    row unpinned under the pointer — or shut by `close()` — stays shut
 *    until the pointer leaves it.
 *  - Escape folds a hover preview (and claims the key, so a pin stack
 *    listening on `window` lets its pins be for that press).
 *  - Dead zones: a pointer over an element inside the row marked
 *    `data-hover-intent="ignore"` (`HOVER_INTENT_IGNORE`) — say, the row's own
 *    action buttons — is still inside the row (it never counts as a leave),
 *    but it never arms: moving onto one cancels a pending open at once, and
 *    a preview already open stays as it is. Moving back onto the rest of the
 *    row starts the wait afresh.
 *  - Only real hover counts: a mouse on a device that hovers. Touch screens
 *    also send compatibility "mouse" events parked where the last tap landed;
 *    those are ignored, so a touch UI owns its own taps.
 *
 * Timers are cleared on unmount. Keyboard focus is the caller's to handle.
 */
import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
	type PointerEvent,
} from 'react';
import { isInsideDialog } from '@/shared/lib/keyboard';

/** Spread on an element inside a hover-intent row to make it a dead zone. */
export const HOVER_INTENT_IGNORE = { 'data-hover-intent': 'ignore' } as const;

/** Is this pointer event over a dead zone (see `HOVER_INTENT_IGNORE`)? */
function overDeadZone(event: PointerEvent): boolean {
	const target = event.target as Element | null;
	return Boolean(target?.closest?.('[data-hover-intent="ignore"]'));
}

/** The hover-intent timings, in one place. */
export const HOVER_INTENT = {
	/** How long the pointer rests on a row before it opens. */
	openDelayMs: 250,
	/** How long a row stays open after the pointer leaves it. */
	closeGraceMs: 150,
	/** How long after the last scroll hover stays off. */
	scrollQuietMs: 300,
	/** How long after a row opens or shuts its siblings may still be moving
	 * (the reveal's longest run, with a frame or two to spare). */
	layoutSettleMs: 320,
	/** The longest a row left for an arming sibling waits for it to open. */
	handoffMaxMs: 600,
} as const;

/** One hook's face to its siblings. */
interface Member {
	/** Shut now, unless the pointer is on it (a handoff). */
	yield: () => void;
}

/** One watch per scroll container, shared by every hook in it: when it last
 * scrolled, whether the pointer has moved since, and the siblings' shared
 * state — who is arming, who is handing off, and when the layout last moved. */
interface ScrollWatch {
	users: number;
	lastScrollAt: number;
	awaitingMove: boolean;
	onScroll: Set<() => void>;
	/** Siblings with an open pending under a resting pointer. */
	arming: Set<Member>;
	/** Siblings left for an arming sibling: they shut when it opens or gives up. */
	handingOff: Set<Member>;
	/** Until when rows may still be growing or folding. */
	layoutMovingUntil: number;
	/** Leaves waiting on the pointer's next real movement. */
	onRealMove: Set<() => void>;
	detach: () => void;
}

const watches = new Map<EventTarget, ScrollWatch>();

function scrollContainerOf(el: Element | null): EventTarget {
	for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
		const { overflowY } = getComputedStyle(node);
		if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') return node;
	}
	return window;
}

function watchScroll(container: EventTarget): ScrollWatch {
	const existing = watches.get(container);
	if (existing) {
		existing.users += 1;
		return existing;
	}
	const watch: ScrollWatch = {
		users: 1,
		lastScrollAt: Number.NEGATIVE_INFINITY,
		awaitingMove: false,
		onScroll: new Set(),
		arming: new Set(),
		handingOff: new Set(),
		layoutMovingUntil: Number.NEGATIVE_INFINITY,
		onRealMove: new Set(),
		detach: () => {},
	};
	const scrolled = () => {
		watch.lastScrollAt = performance.now();
		watch.awaitingMove = true;
		for (const fn of watch.onScroll) fn();
	};
	const moved = (event: Event) => {
		const { movementX, movementY } = event as globalThis.PointerEvent;
		if (!movementX && !movementY) return;
		watch.awaitingMove = false;
		const waiting = [...watch.onRealMove];
		watch.onRealMove.clear();
		for (const fn of waiting) fn();
	};
	const passive = { passive: true } as const;
	container.addEventListener('scroll', scrolled, passive);
	container.addEventListener('wheel', scrolled, passive);
	window.addEventListener('pointermove', moved, passive);
	watch.detach = () => {
		container.removeEventListener('scroll', scrolled);
		container.removeEventListener('wheel', scrolled);
		window.removeEventListener('pointermove', moved);
	};
	watches.set(container, watch);
	return watch;
}

function releaseScroll(container: EventTarget, watch: ScrollWatch) {
	watch.users -= 1;
	if (watch.users > 0) return;
	watch.detach();
	watches.delete(container);
}

/** A mouse on a device that can hover — not a touch screen's stand-in events. */
function realHover(event: PointerEvent): boolean {
	if (event.pointerType !== 'mouse') return false;
	return (
		typeof window.matchMedia !== 'function' ||
		window.matchMedia('(hover: hover) and (pointer: fine)').matches
	);
}

/** Is the pointer really over `el` now? `:hover` follows the pointer's real
 * position; where it can't be read, trust the leave. */
function pointerOver(el: Element | null): boolean {
	if (!el) return false;
	try {
		return el.matches(':hover');
	} catch {
		return false;
	}
}

export interface HoverIntentOptions {
	openDelayMs?: number;
	closeGraceMs?: number;
	scrollQuietMs?: number;
	/** The caller holds this row open (a pin): hover leaves it alone. */
	pinned?: boolean;
}

export function useHoverIntent<T extends HTMLElement>({
	openDelayMs = HOVER_INTENT.openDelayMs,
	closeGraceMs = HOVER_INTENT.closeGraceMs,
	scrollQuietMs = HOVER_INTENT.scrollQuietMs,
	pinned = false,
}: HoverIntentOptions = {}) {
	const ref = useRef<T>(null);
	const [open, setOpenState] = useState(false);
	const openRef = useRef(false);
	const pinnedRef = useRef(pinned);
	const inside = useRef(false);
	/** Shut by click or `close()` while the pointer is on it: stays shut until it leaves. */
	const dismissed = useRef(false);
	const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const watch = useRef<ScrollWatch | null>(null);
	/** This hook, as its siblings see it (stable; its body is set below). */
	const member = useRef<Member>({ yield: () => {} });
	/** A leave reported while the layout moved, waiting to be confirmed. */
	const pendingLeave = useRef<(() => void) | null>(null);

	const noteLayoutMoving = useCallback(() => {
		const w = watch.current;
		if (w) w.layoutMovingUntil = performance.now() + HOVER_INTENT.layoutSettleMs;
	}, []);
	const setOpen = useCallback(
		(next: boolean) => {
			if (openRef.current !== next) noteLayoutMoving();
			openRef.current = next;
			setOpenState(next);
		},
		[noteLayoutMoving],
	);

	/** Siblings left for this one: shut them now (this one opened, or gave up). */
	const releaseHandoffs = useCallback(() => {
		const w = watch.current;
		if (!w || w.arming.size > 0) return;
		const waiting = [...w.handingOff];
		w.handingOff.clear();
		for (const m of waiting) m.yield();
	}, []);
	const clearOpenTimer = useCallback(() => {
		if (openTimer.current) clearTimeout(openTimer.current);
		openTimer.current = null;
		const w = watch.current;
		if (w?.arming.delete(member.current)) releaseHandoffs();
	}, [releaseHandoffs]);
	const clearCloseTimer = useCallback(() => {
		if (closeTimer.current) clearTimeout(closeTimer.current);
		closeTimer.current = null;
		if (settleTimer.current) clearTimeout(settleTimer.current);
		settleTimer.current = null;
		watch.current?.handingOff.delete(member.current);
	}, []);
	const dropPendingLeave = useCallback(() => {
		if (pendingLeave.current) watch.current?.onRealMove.delete(pendingLeave.current);
		pendingLeave.current = null;
	}, []);

	const suppressed = useCallback(() => {
		const w = watch.current;
		if (!w) return false;
		return w.awaitingMove || performance.now() - w.lastScrollAt < scrollQuietMs;
	}, [scrollQuietMs]);

	/** Opened by hover: siblings left for this one fold in the same frame. */
	const openByHover = useCallback(() => {
		setOpen(true);
		const w = watch.current;
		if (!w) return;
		w.arming.delete(member.current);
		const waiting = [...w.handingOff];
		w.handingOff.clear();
		for (const m of waiting) if (m !== member.current) m.yield();
	}, [setOpen]);

	/** (Re)start the rest timer — unless scrolling holds hover off. */
	const arm = useCallback(() => {
		if (openTimer.current) clearTimeout(openTimer.current);
		openTimer.current = null;
		if (openRef.current || pinnedRef.current || dismissed.current || suppressed()) {
			clearOpenTimer();
			return;
		}
		watch.current?.arming.add(member.current);
		openTimer.current = setTimeout(() => {
			openTimer.current = null;
			if (inside.current && !pinnedRef.current && !dismissed.current && !suppressed()) {
				openByHover();
			} else clearOpenTimer();
		}, openDelayMs);
	}, [clearOpenTimer, suppressed, openDelayMs, openByHover]);

	/** The pointer has really gone: stop arming, and fold after the grace. */
	const leave = useCallback(() => {
		dropPendingLeave();
		inside.current = false;
		dismissed.current = false;
		clearOpenTimer();
		if (!openRef.current || pinnedRef.current) return;
		clearCloseTimer();
		closeTimer.current = setTimeout(() => {
			closeTimer.current = null;
			const w = watch.current;
			const others = w ? [...w.arming].filter((m) => m !== member.current) : [];
			if (!w || others.length === 0) {
				setOpen(false);
				return;
			}
			// A sibling is about to open: fold when it does, so the two move as one.
			w.handingOff.add(member.current);
			settleTimer.current = setTimeout(() => {
				settleTimer.current = null;
				w.handingOff.delete(member.current);
				if (!inside.current && !pinnedRef.current) setOpen(false);
			}, HOVER_INTENT.handoffMaxMs);
		}, closeGraceMs);
	}, [dropPendingLeave, clearOpenTimer, clearCloseTimer, closeGraceMs, setOpen]);

	useEffect(() => {
		member.current.yield = () => {
			// Under the pointer: a handoff never takes it away.
			if (inside.current && !pendingLeave.current) return;
			clearCloseTimer();
			setOpen(false);
		};
	}, [clearCloseTimer, setOpen]);

	// The pin is the caller's. Pinned, the row is open for its own reasons:
	// the preview, any timers and any handoff waiting on it go (siblings left
	// for this one fold with it, in the same frame it opens). Unpinned under
	// the pointer, it stays shut until the pointer leaves.
	const wasPinned = useRef(pinned);
	useLayoutEffect(() => {
		pinnedRef.current = pinned;
		if (wasPinned.current === pinned) return;
		wasPinned.current = pinned;
		clearOpenTimer();
		clearCloseTimer();
		dropPendingLeave();
		noteLayoutMoving();
		if (pinned) {
			dismissed.current = false;
			openRef.current = false;
			setOpenState(false);
			const w = watch.current;
			if (w) {
				const waiting = [...w.handingOff];
				w.handingOff.clear();
				for (const m of waiting) if (m !== member.current) m.yield();
			}
		} else {
			dismissed.current = inside.current;
		}
	}, [pinned, clearOpenTimer, clearCloseTimer, dropPendingLeave, noteLayoutMoving]);

	useEffect(() => {
		const container = scrollContainerOf(ref.current);
		const w = watchScroll(container);
		const self = member.current;
		watch.current = w;
		// A scroll cancels a pending open; the pointer must move to arm again.
		w.onScroll.add(clearOpenTimer);
		return () => {
			w.onScroll.delete(clearOpenTimer);
			dropPendingLeave();
			clearOpenTimer();
			clearCloseTimer();
			w.arming.delete(self);
			w.handingOff.delete(self);
			releaseScroll(container, w);
			watch.current = null;
		};
	}, [clearOpenTimer, clearCloseTimer, dropPendingLeave]);

	/** Shut the preview at once, and keep it shut while the pointer stays on it. */
	const close = useCallback(() => {
		clearOpenTimer();
		clearCloseTimer();
		dismissed.current = inside.current;
		setOpen(false);
	}, [clearOpenTimer, clearCloseTimer, setOpen]);

	// Escape folds a hover preview — unless a dialog has it — and claims the
	// press, so a pin stack listening on `window` keeps its pins for it.
	useEffect(() => {
		if (!open) return;
		const onKey = (event: KeyboardEvent) => {
			if (event.key !== 'Escape' || event.defaultPrevented) return;
			if (isInsideDialog(event.target)) return;
			event.preventDefault();
			close();
		};
		document.addEventListener('keydown', onKey);
		return () => document.removeEventListener('keydown', onKey);
	}, [open, close]);

	const onPointerEnter = useCallback(
		(event: PointerEvent) => {
			if (!realHover(event)) return;
			inside.current = true;
			dropPendingLeave();
			// Entering only cancels a pending close: the browser also reports an
			// enter when content moves under a still cursor, so movement arms.
			clearCloseTimer();
			if (overDeadZone(event)) clearOpenTimer();
		},
		[clearCloseTimer, clearOpenTimer, dropPendingLeave],
	);
	const onPointerMove = useCallback(
		(event: PointerEvent) => {
			if (!realHover(event)) return;
			inside.current = true;
			if (!event.movementX && !event.movementY) return;
			dropPendingLeave();
			// Real movement ends the scroll's hold (the window listener would
			// too, but only after this handler has run).
			if (watch.current) watch.current.awaitingMove = false;
			// A dead zone: no wait runs here, and a preview stays as it is.
			if (overDeadZone(event)) {
				clearOpenTimer();
				return;
			}
			if (!openRef.current && !pinnedRef.current) arm();
		},
		[arm, clearOpenTimer, dropPendingLeave],
	);
	const onPointerLeave = useCallback(
		(event: PointerEvent) => {
			if (!realHover(event)) return;
			const w = watch.current;
			if (!w || performance.now() >= w.layoutMovingUntil) {
				leave();
				return;
			}
			// The layout is moving: this may be the row sliding away from a
			// still pointer. Decide at the next real movement, or once it settles.
			dropPendingLeave();
			const check = () => {
				if (pendingLeave.current !== check) return;
				pendingLeave.current = null;
				if (!pointerOver(ref.current)) leave();
			};
			pendingLeave.current = check;
			w.onRealMove.add(check);
			setTimeout(check, Math.max(0, w.layoutMovingUntil - performance.now()) + 16);
		},
		[leave, dropPendingLeave],
	);

	return {
		ref,
		/** The hover preview (never set while pinned). */
		open,
		close,
		handlers: { onPointerEnter, onPointerMove, onPointerLeave },
	};
}
