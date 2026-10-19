/**
 * SheetPrimitive
 *
 * Low-level accessible sheet/drawer that slides in from an edge of the
 * viewport. Keep this file dumb — no business logic, no data fetching,
 * just focus management + animation + portaling.
 *
 * Behaviour:
 *   - Slides from right (default), left, or bottom
 *   - Focus trap + restoration to the trigger on close
 *   - Escape + backdrop click close (opt out of both with `preventClose`, of
 *     the backdrop alone with `dismissOnBackdrop={false}`); Escape is left to a
 *     native modal `<dialog>` open above the sheet
 *   - Body scroll lock via `overscroll-behavior: contain`
 *   - ARIA dialog semantics
 *   - Children unmount on close; `keepMounted` holds a form's draft
 *   - `size` picks the side-panel width (`sm` forms, `md` previews)
 *
 * `SheetHeader` / `SheetBody` / `SheetFooter` are optional borderless layout
 * slots for a `flex flex-col` panel; `SheetCloseButton` is the header's ✕.
 */

import {
	useEffect,
	useId,
	useLayoutEffect,
	useRef,
	useCallback,
	useState,
	type ReactNode,
	type RefObject,
	type JSX,
	type HTMLAttributes,
	type ButtonHTMLAttributes,
} from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { Button } from '@/shared/ui/Button';
import { useCoversRightEdge } from '@/shared/ui/rightEdge';

export interface SheetPrimitiveProps {
	/** Whether the sheet is open. */
	open: boolean;
	/** Fired when the sheet should close (Escape, backdrop click, etc.). */
	onClose: () => void;
	/** Sheet content. */
	children: ReactNode;
	/** Which side the sheet slides from. */
	side?: 'right' | 'bottom' | 'left';
	/**
	 * Panel width for a side sheet: `sm` (480px, the default — forms), `md`
	 * (36rem — previews) or `flow` (880px — a multi-step flow). A width in
	 * `className` still wins over any.
	 */
	size?: 'sm' | 'md' | 'flow';
	/** Sheets sharing a key are steps of one flow and swap in place on hand-off. */
	handoffKey?: string;
	/** Extra classes for the sheet panel. */
	className?: string;
	/** Extra classes for the backdrop overlay. */
	overlayClassName?: string;
	/** If true, clicking outside / Escape will NOT close the sheet. */
	preventClose?: boolean;
	/**
	 * If `false`, clicking the backdrop will NOT close the sheet — Escape and the
	 * sheet's own close controls still do. Same name and meaning as `Dialog`'s.
	 */
	dismissOnBackdrop?: boolean;
	/**
	 * Keep `children` mounted (but `hidden`) while closed, so a form's draft survives
	 * a dismissal. Leave off for a sheet holding a secret mid-entry.
	 */
	keepMounted?: boolean;
	/** Ref to the element that should receive focus when the sheet opens. */
	initialFocus?: RefObject<HTMLElement | null>;
	/** Fired after the closing animation has fully completed. */
	onAfterClose?: () => void;
	/** ARIA label for the sheet. */
	ariaLabel?: string;
	/** ID of the element that labels the sheet (preferred over ariaLabel). */
	ariaLabelledBy?: string;
}

const FOCUSABLE_SELECTOR = [
	'button:not([disabled])',
	'[href]',
	'input:not([disabled])',
	'select:not([disabled])',
	'textarea:not([disabled])',
	'[tabindex]:not([tabindex="-1"])',
].join(', ');

/** `offsetParent` is also null for `position: fixed`, so those are judged by their boxes. */
function isRendered(el: HTMLElement): boolean {
	if (el.offsetParent != null) return true;
	return getComputedStyle(el).position === 'fixed' && el.getClientRects().length > 0;
}

/** Focusable controls in DOM order, skipping inert/unrendered panes so Tab can't escape the dialog. */
function focusableIn(root: HTMLElement | null): HTMLElement[] {
	if (!root) return [];
	return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
		(el) => el.closest('[inert]') == null && isRendered(el),
	);
}

/**
 * How long the sheet stays mounted after `open` goes false — long enough for
 * the exit slide (which itself runs a little shorter) to finish. Exported so
 * tests and stacked-sheet hand-offs wait on the real number, not a guess.
 */
export const SHEET_EXIT_MS = 220;

const SIDE_STYLES = {
	right: {
		container: 'inset-y-0 inset-x-0 sm:left-auto sm:right-0',
		panel: 'h-full w-full max-w-full',
		shadow: 'shadow-[-24px_0_60px_-20px_hsl(var(--shadow)/calc(.7*var(--shadow-k)))]',
		enter: 'translate-x-0',
		exit: 'translate-x-full',
	},
	left: {
		container: 'inset-y-0 inset-x-0 sm:right-auto sm:left-0',
		panel: 'h-full w-full max-w-full',
		shadow: 'shadow-[24px_0_60px_-20px_hsl(var(--shadow)/calc(.7*var(--shadow-k)))]',
		enter: 'translate-x-0',
		exit: '-translate-x-full',
	},
	bottom: {
		container: 'inset-x-0 bottom-0',
		panel: 'flex max-h-[85dvh] w-full flex-col overflow-hidden rounded-t-xl',
		shadow: 'shadow-[0_-24px_60px_-20px_hsl(var(--shadow)/calc(.7*var(--shadow-k)))]',
		enter: 'translate-y-0',
		exit: 'translate-y-full',
	},
};

/** Side-panel widths (left/right only; a bottom sheet is always full width). */
const SIZE_WIDTHS = {
	// Forms: the long-standing 480px panel.
	sm: 'sm:w-[480px] sm:max-w-[90vw]',
	// Read-only previews (e.g. the catalog API sheet) get a little more room.
	md: 'sm:w-[min(36rem,100vw)]',
	flow: 'sm:w-[880px] sm:max-w-[calc(100vw-2rem)]',
} as const;

/**
 * Hand-offs between sheets of one flow (`handoffKey`): the leaving sheet holds its
 * place and the arriving one appears in it with no slide or scrim fade.
 */
interface HandoffEntry {
	heldAt: number;
	closedAt: number;
	openedAt: number;
	/** A sheet closing right after its OWN open is the flow ending, not a hand-off. */
	openedBy: string | null;
	returnFocus: HTMLElement | null;
	release: Set<() => void>;
}
const handoffs = new Map<string, HandoffEntry>();

/** Prune idle flows: `returnFocus` would otherwise pin a detached opener's tree for the module's life. */
function pruneHandoffs(): void {
	const now = performance.now();
	for (const [key, entry] of handoffs) {
		if (entry.release.size > 0) continue;
		const last = Math.max(entry.heldAt, entry.closedAt, entry.openedAt);
		if (now - last >= HANDOFF_HOLD_MS) handoffs.delete(key);
	}
}

const handoffFor = (key: string): HandoffEntry => {
	pruneHandoffs();
	let entry = handoffs.get(key);
	if (!entry) {
		entry = {
			heldAt: -Infinity,
			closedAt: -Infinity,
			openedAt: -Infinity,
			openedBy: null,
			returnFocus: null,
			release: new Set(),
		};
		handoffs.set(key, entry);
	}
	return entry;
};
/** A close this soon after a hold, or an open this soon after a close, is a hand-off. */
const HANDOFF_WINDOW_MS = 120;
const HANDOFF_HOLD_MS = 2000;

/** Call right before closing a flow sheet whose next step opens another of the same `handoffKey`. */
export function holdSheetForHandoff(key: string): void {
	handoffFor(key).heldAt = performance.now();
}

/** Tear a flow down: holding sheets and snapshots step aside at once, and the flow is forgotten. */
export function endSheetHandoff(key: string): void {
	const entry = handoffs.get(key);
	if (!entry) return;
	for (const release of [...entry.release]) release();
	entry.release.clear();
	entry.returnFocus = null;
	handoffs.delete(key);
}

export function SheetPrimitive({
	open,
	onClose,
	children,
	side = 'right',
	size = 'sm',
	className,
	overlayClassName,
	preventClose = false,
	dismissOnBackdrop = true,
	keepMounted = false,
	handoffKey,
	initialFocus,
	onAfterClose,
	ariaLabel,
	ariaLabelledBy,
}: SheetPrimitiveProps): JSX.Element | null {
	const sheetRef = useRef<HTMLDivElement>(null);
	const instanceId = useId();
	const previousFocusRef = useRef<HTMLElement | null>(null);
	const [animationState, setAnimationState] = useState<
		'closed' | 'entering' | 'open' | 'exiting'
	>('closed');
	const [mounted, setMounted] = useState(false);
	const [holding, setHolding] = useState(false);
	const [instant, setInstant] = useState(false);

	const styles = SIDE_STYLES[side];

	// A right-hand sheet covers the right edge from its first frame until it starts
	// closing, so corner overlays (toasts) move beside it instead of over its
	// footer actions.
	useCoversRightEdge(sheetRef, side === 'right' && open && animationState !== 'closed');

	useEffect(() => {
		setMounted(true);
	}, []);

	// `open` is the only dependency — animationState is the internal state we drive.
	// Including it would create races between user toggles and animation timers.
	// Before paint, so a hand-off swaps the sheets within one frame.
	useLayoutEffect(() => {
		const key = handoffKey;
		const now = performance.now();
		if (open) {
			// Reopened while holding for a next step that never came: stay open.
			if (holding) {
				setHolding(false);
				return;
			}
			if (key) {
				const entry = handoffFor(key);
				entry.openedAt = now;
				entry.openedBy = instanceId;
				// Another sheet of the flow is on screen or just left: take its place in place.
				const taking =
					entry.release.size > 0 ||
					now - entry.closedAt < HANDOFF_WINDOW_MS ||
					document.querySelector(`[data-handoff-key="${CSS.escape(key)}"]`) != null;
				if (taking && animationState === 'closed') {
					previousFocusRef.current = entry.returnFocus;
					entry.returnFocus = null;
					for (const release of [...entry.release]) release();
					setInstant(true);
					setAnimationState('open');
					return;
				}
			}
			// Re-opened mid-exit (e.g. the setup queue's Back straight after the tray
			// handed over): enter again rather than finishing the exit and staying shut.
			if (animationState === 'closed' || animationState === 'exiting') {
				setAnimationState('entering');
			}
		} else {
			if (animationState === 'open' || animationState === 'entering') {
				if (key) {
					const entry = handoffFor(key);
					entry.closedAt = now;
					entry.returnFocus = previousFocusRef.current ?? entry.returnFocus;
					// The next sheet already took over in this commit: step aside at once.
					if (entry.openedBy !== instanceId && now - entry.openedAt < HANDOFF_WINDOW_MS) {
						previousFocusRef.current = null;
						setAnimationState('closed');
						onAfterCloseRef.current?.();
						return;
					}
					if (now - entry.heldAt < HANDOFF_HOLD_MS) {
						entry.heldAt = -Infinity;
						setHolding(true);
						return;
					}
				}
				setAnimationState('exiting');
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	useLayoutEffect(() => {
		if (!handoffKey || !(holding || animationState === 'exiting')) return;
		const entry = handoffFor(handoffKey);
		const release = (): void => {
			previousFocusRef.current = null;
			setHolding(false);
			setAnimationState('closed');
			onAfterCloseRef.current?.();
		};
		// The next sheet opened in this same commit, before this one registered.
		if (
			entry.openedBy !== instanceId &&
			entry.openedAt >= entry.closedAt &&
			performance.now() - entry.openedAt < HANDOFF_WINDOW_MS
		) {
			release();
			return;
		}
		entry.release.add(release);
		const timer = holding
			? setTimeout(() => {
					setHolding(false);
					setAnimationState('exiting');
				}, HANDOFF_HOLD_MS)
			: undefined;
		return (): void => {
			entry.release.delete(release);
			if (timer) clearTimeout(timer);
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps -- identity is stable
	}, [handoffKey, holding, animationState]);

	// Unmounted while holding: leave an inert snapshot so the next sheet has a place to take.
	const rootRef = useRef<HTMLDivElement>(null);
	const holdingRef = useRef(holding);
	holdingRef.current = holding || animationState === 'open';
	useLayoutEffect(() => {
		const root = rootRef;
		const held = holdingRef;
		return (): void => {
			if (!handoffKey || !held.current || !root.current) return;
			const entry = handoffFor(handoffKey);
			const asked = performance.now() - entry.heldAt < HANDOFF_HOLD_MS;
			if (!asked && !root.current.querySelector('[data-handoff="holding"]')) return;
			entry.heldAt = -Infinity;
			const ghost = root.current.cloneNode(true) as HTMLElement;
			ghost.setAttribute('inert', '');
			ghost.setAttribute('aria-hidden', 'true');
			ghost.querySelectorAll('[role="dialog"]').forEach((d) => d.removeAttribute('role'));
			// Strip every `id` and `data-*`: duplicates make ARIA targets and test-id
			// queries ambiguous.
			for (const el of [ghost, ...ghost.querySelectorAll<HTMLElement>('*')]) {
				el.removeAttribute('id');
				for (const name of el.getAttributeNames()) {
					if (name.startsWith('data-')) el.removeAttribute(name);
				}
			}
			document.body.appendChild(ghost);
			// Registered on the flow so the next sheet (or `endSheetHandoff`) removes it
			// and the timer never touches `document.body` after this tree is gone.
			let timer: ReturnType<typeof setTimeout> | undefined;
			const remove = (): void => {
				if (timer != null) clearTimeout(timer);
				timer = undefined;
				ghost.remove();
				entry.release.delete(remove);
			};
			entry.release.add(remove);
			timer = setTimeout(remove, HANDOFF_HOLD_MS);
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps -- on unmount only
	}, []);

	// Drop the no-transition flag after paint so this sheet's own close still slides.
	useEffect(() => {
		if (!instant) return;
		const timer = setTimeout(() => setInstant(false), 320);
		return (): void => clearTimeout(timer);
	}, [instant]);

	// `onAfterClose` lives in a ref so callers can pass an inline closure
	// without resetting the exit timer on every parent render.
	const onAfterCloseRef = useRef(onAfterClose);
	useEffect(() => {
		onAfterCloseRef.current = onAfterClose;
	}, [onAfterClose]);

	useEffect(() => {
		if (animationState === 'entering') {
			// Double rAF: first frame paints with `exit` transform, next frame swaps
			// to `enter` so the transition fires. Single rAF flickers in Chromium.
			let cancelled = false;
			const enterTimer = requestAnimationFrame(() => {
				requestAnimationFrame(() => {
					if (!cancelled) {
						setAnimationState((s) => (s === 'entering' ? 'open' : s));
					}
				});
			});
			return (): void => {
				cancelled = true;
				cancelAnimationFrame(enterTimer);
			};
		}

		if (animationState === 'exiting') {
			const exitTimer = setTimeout(() => {
				setAnimationState('closed');
				onAfterCloseRef.current?.();
			}, SHEET_EXIT_MS);
			return (): void => clearTimeout(exitTimer);
		}
	}, [animationState]);

	// The focus to hand back on close: whatever held it as the sheet began to
	// enter. Keyed on `animationState` alone — a host that swaps `initialFocus`
	// mid-entrance (a tab switch) must not re-capture, or the return target
	// becomes a control inside this sheet, gone once it unmounts.
	useEffect(() => {
		if (animationState !== 'entering') return;
		const active = document.activeElement as HTMLElement | null;
		if (sheetRef.current?.contains(active)) return;
		previousFocusRef.current = active;
	}, [animationState]);

	useEffect(() => {
		if (animationState === 'open') {
			const timer = setTimeout(() => {
				// Fires well after the sheet is usable: anything already focused inside it
				// is where the user put focus, and moving it would eat their keystrokes.
				const current = document.activeElement;
				if (sheetRef.current?.contains(current)) return;
				// Nor may it pull focus back out of somewhere the user has moved it since
				// this sheet began opening — e.g. a second sheet stacked on top (the setup
				// queue's credential form), which portals outside this one's DOM.
				if (current && current !== document.body && current !== previousFocusRef.current) {
					return;
				}
				if (initialFocus?.current) {
					initialFocus.current.focus();
				} else {
					focusableIn(sheetRef.current)[0]?.focus();
				}
			}, 50);
			return (): void => clearTimeout(timer);
		}
	}, [animationState, initialFocus]);

	useEffect(() => {
		if (animationState === 'closed' && previousFocusRef.current) {
			const elementToFocus = previousFocusRef.current;
			previousFocusRef.current = null;
			// The flow ended here: release the module-scope reference to the element.
			const entry = handoffKey ? handoffs.get(handoffKey) : undefined;
			if (entry?.returnFocus === elementToFocus) entry.returnFocus = null;
			const timer = setTimeout(() => {
				// A hand-off between overlays (the Add-APIs tray ↔ setup queue) has
				// already put focus in the next one; restoring here would yank it
				// back out to a trigger behind that overlay.
				const current = document.activeElement;
				if (
					current &&
					current !== document.body &&
					current.closest('[role="dialog"], dialog[open]')
				) {
					return;
				}
				if (elementToFocus?.isConnected) elementToFocus.focus();
			}, 10);
			return (): void => clearTimeout(timer);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps -- the key is stable per flow
	}, [animationState]);

	// `overscroll-behavior: contain` is the modern scroll-lock.
	useEffect(() => {
		if (animationState !== 'closed') {
			document.documentElement.style.setProperty('overscroll-behavior', 'contain');
			document.body.style.setProperty('overscroll-behavior', 'contain');
		} else {
			document.documentElement.style.removeProperty('overscroll-behavior');
			document.body.style.removeProperty('overscroll-behavior');
		}
	}, [animationState]);

	useEffect(() => {
		return (): void => {
			document.documentElement.style.removeProperty('overscroll-behavior');
			document.body.style.removeProperty('overscroll-behavior');
		};
	}, []);

	const handleKeyDown = useCallback(
		(e: KeyboardEvent) => {
			// Escape is honoured from the moment the sheet mounts, including during the
			// entrance. Tab containment waits for `open`, where there is something to cycle.
			if (animationState !== 'open' && animationState !== 'entering') return;
			if (e.key !== 'Escape' && animationState !== 'open') return;

			if (e.key === 'Escape') {
				// A native modal <dialog> renders in the top layer, ABOVE any sheet, and the
				// browser turns an unprevented Escape into its cancel — so the sheet must
				// neither preventDefault nor close itself underneath the dialog.
				if (document.querySelector('dialog:modal')) return;
				if (!preventClose) {
					e.preventDefault();
					onClose();
				}
				return;
			}

			if (e.key === 'Tab') {
				const focusable = focusableIn(sheetRef.current);
				if (!focusable.length) return;

				const first = focusable[0];
				const last = focusable[focusable.length - 1];

				if (e.shiftKey && document.activeElement === first) {
					e.preventDefault();
					last.focus();
				} else if (!e.shiftKey && document.activeElement === last) {
					e.preventDefault();
					first.focus();
				}
			}
		},
		[animationState, onClose, preventClose],
	);

	useEffect(() => {
		document.addEventListener('keydown', handleKeyDown);
		return (): void => document.removeEventListener('keydown', handleKeyDown);
	}, [handleKeyDown]);

	const handleBackdropClick = useCallback(() => {
		if (!preventClose && dismissOnBackdrop && animationState === 'open') {
			onClose();
		}
	}, [preventClose, dismissOnBackdrop, onClose, animationState]);

	if (!mounted) return null;
	const isClosed = animationState === 'closed';
	if (isClosed && !keepMounted) return null;

	const isVisible = animationState === 'open';
	const isExiting = animationState === 'exiting';

	return createPortal(
		<div
			ref={rootRef}
			hidden={isClosed}
			className={cn('fixed inset-0 z-50', isClosed && 'hidden')}
			style={{ overscrollBehavior: 'contain' }}
		>
			<div
				className={cn(
					// Page-tinted scrim with a light blur, rather than flat black.
					'bg-scrim absolute inset-0 overflow-hidden backdrop-blur-[3px]',
					'ease-out-soft transition-opacity duration-[220ms]',
					'motion-reduce:duration-[10ms]',
					instant && 'transition-none',
					isVisible ? 'opacity-100' : 'opacity-0',
					overlayClassName,
				)}
				style={{ overscrollBehavior: 'contain' }}
				onClick={handleBackdropClick}
				aria-hidden="true"
				data-testid={isClosed ? undefined : 'sheet-backdrop'}
			/>

			<div className={cn('fixed max-w-full', styles.container)}>
				<div
					ref={sheetRef}
					role="dialog"
					aria-modal="true"
					// On the panel too: "is an overlay open?" is asked of the dialog node itself.
					hidden={isClosed}
					aria-label={ariaLabel}
					aria-labelledby={ariaLabelledBy}
					className={cn(
						'bg-surface-sheet overflow-x-hidden [--field-bg:var(--surface-field)]',
						styles.shadow,
						// Slides in on a soft ease-out; leaves faster, accelerating away.
						isExiting
							? 'ease-in-exit transition-[translate,transform,opacity] duration-[180ms]'
							: 'ease-out-soft transition-[translate,transform,opacity] duration-[220ms]',
						// Reduced motion: no slide, just a near-instant fade.
						'motion-reduce:translate-none motion-reduce:duration-[10ms]',
						!isVisible && 'motion-reduce:opacity-0',
						styles.panel,
						side !== 'bottom' && SIZE_WIDTHS[size],
						isVisible ? styles.enter : styles.exit,
						// A hand-off: in place at once; only the content fades in.
						instant &&
							'transition-none [&>*]:animate-[j1-page-out_200ms_var(--ease-out-soft)_reverse_both]',
						className,
					)}
					style={{
						willChange: 'transform',
						overscrollBehavior: 'contain',
					}}
					// Tagged only while on screen — a closed `keepMounted` sheet is not open.
					data-testid={isClosed ? undefined : 'sheet-primitive'}
					data-handoff-key={isClosed || !handoffKey ? undefined : handoffKey}
					data-handoff={instant ? 'in' : holding ? 'holding' : undefined}
				>
					{children}
				</div>
			</div>
		</div>,
		document.body,
	);
}

type SlotProps = HTMLAttributes<HTMLElement>;

/**
 * Top band of a sheet (identity + close). Borderless: the panel's tonal steps
 * separate the regions, not rules. Put it inside a `flex flex-col` panel.
 * A plain `div`, not `<header>`: inside a dialog a `<header>` can surface as a
 * second `banner` landmark beside the app's own.
 */
export function SheetHeader({ className, ...props }: SlotProps): JSX.Element {
	return (
		<div
			data-sheet-header=""
			className={cn('flex shrink-0 items-start gap-4 px-5 pt-5 pb-[18px]', className)}
			{...props}
		/>
	);
}

export type SheetCloseButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'>;

/**
 * The ✕ that closes a form sheet, aligned to the top-right of a `SheetHeader`.
 * A 40px touch target on mobile, 32px from `sm` up. Its accessible name
 * defaults to "Close".
 */
export function SheetCloseButton({
	className,
	'aria-label': ariaLabel = 'Close',
	...props
}: SheetCloseButtonProps): JSX.Element {
	return (
		<Button
			variant="ghost"
			size="icon"
			aria-label={ariaLabel}
			className={cn('-mt-1 -mr-2 h-10 w-10 shrink-0 p-0 sm:h-8 sm:w-8', className)}
			{...props}
		>
			<X className="h-4 w-4" aria-hidden="true" />
		</Button>
	);
}

/** The scrolling middle of a sheet; grows to fill the space between header and footer. */
export function SheetBody({ className, ...props }: HTMLAttributes<HTMLDivElement>): JSX.Element {
	return (
		<div
			className={cn('min-h-0 flex-1 overflow-y-auto px-5 pt-1 pb-5', className)}
			{...props}
		/>
	);
}

/** Action band pinned to the sheet's bottom: a slightly darker strip, actions right-aligned. */
export function SheetFooter({ className, ...props }: SlotProps): JSX.Element {
	return (
		<footer
			className={cn(
				'bg-surface-sheet-foot border-hairline-field flex shrink-0 items-center justify-end gap-3.5 border-t px-5 py-3.5',
				className,
			)}
			{...props}
		/>
	);
}
