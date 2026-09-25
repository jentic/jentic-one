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
 */

import {
	useEffect,
	useRef,
	useCallback,
	useState,
	type ReactNode,
	type RefObject,
	type JSX,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/shared/lib/utils';
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

const ANIMATION_DURATION = 300;

const SIDE_STYLES = {
	right: {
		container: 'inset-y-0 inset-x-0 sm:left-auto sm:right-0',
		panel: 'h-full w-full max-w-full sm:w-[480px] sm:max-w-[90vw]',
		enter: 'translate-x-0',
		exit: 'translate-x-full',
	},
	left: {
		container: 'inset-y-0 inset-x-0 sm:right-auto sm:left-0',
		panel: 'h-full w-full max-w-full sm:w-[480px] sm:max-w-[90vw]',
		enter: 'translate-x-0',
		exit: '-translate-x-full',
	},
	bottom: {
		container: 'inset-x-0 bottom-0',
		panel: 'flex max-h-[85dvh] w-full flex-col overflow-hidden rounded-t-xl',
		enter: 'translate-y-0',
		exit: 'translate-y-full',
	},
};

export function SheetPrimitive({
	open,
	onClose,
	children,
	side = 'right',
	className,
	overlayClassName,
	preventClose = false,
	dismissOnBackdrop = true,
	keepMounted = false,
	initialFocus,
	onAfterClose,
	ariaLabel,
	ariaLabelledBy,
}: SheetPrimitiveProps): JSX.Element | null {
	const sheetRef = useRef<HTMLDivElement>(null);
	const previousFocusRef = useRef<HTMLElement | null>(null);
	const [animationState, setAnimationState] = useState<
		'closed' | 'entering' | 'open' | 'exiting'
	>('closed');
	const [mounted, setMounted] = useState(false);

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
	useEffect(() => {
		if (open) {
			// Re-opened mid-exit (e.g. the setup queue's Back straight after the tray
			// handed over): enter again rather than finishing the exit and staying shut.
			if (animationState === 'closed' || animationState === 'exiting') {
				setAnimationState('entering');
			}
		} else {
			if (animationState === 'open' || animationState === 'entering') {
				setAnimationState('exiting');
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	// `onAfterClose` lives in a ref so callers can pass an inline closure
	// without resetting the 300ms exit timer on every parent render.
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
			}, ANIMATION_DURATION);
			return (): void => clearTimeout(exitTimer);
		}
	}, [animationState]);

	useEffect(() => {
		if (animationState === 'entering') {
			previousFocusRef.current = document.activeElement as HTMLElement;
		}

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
					const firstFocusable =
						sheetRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
					firstFocusable?.focus();
				}
			}, 50);
			return (): void => clearTimeout(timer);
		}
	}, [animationState, initialFocus]);

	useEffect(() => {
		if (animationState === 'closed' && previousFocusRef.current) {
			const elementToFocus = previousFocusRef.current;
			previousFocusRef.current = null;
			setTimeout(() => {
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
		}
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
				const focusable =
					sheetRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
				if (!focusable?.length) return;

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

	return createPortal(
		<div
			hidden={isClosed}
			className={cn('fixed inset-0 z-50', isClosed && 'hidden')}
			style={{ overscrollBehavior: 'contain' }}
		>
			<div
				className={cn(
					'absolute inset-0 overflow-hidden bg-black/50 backdrop-blur-sm',
					'transition-opacity duration-300 ease-out',
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
						'bg-card border-border overflow-x-hidden shadow-xl',
						'transition-transform duration-300 ease-out',
						styles.panel,
						side === 'right' && 'border-l',
						side === 'left' && 'border-r',
						side === 'bottom' && 'border-t',
						isVisible ? styles.enter : styles.exit,
						className,
					)}
					style={{
						willChange: 'transform',
						overscrollBehavior: 'contain',
					}}
					// Tagged only while on screen — a closed `keepMounted` sheet is not open.
					data-testid={isClosed ? undefined : 'sheet-primitive'}
				>
					{children}
				</div>
			</div>
		</div>,
		document.body,
	);
}
