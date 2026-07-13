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
 *   - Escape + backdrop click close (opt-out with `preventClose`)
 *   - Body scroll lock via `overscroll-behavior: contain`
 *   - ARIA dialog semantics
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

	useEffect(() => {
		setMounted(true);
	}, []);

	// `open` is the only dependency — animationState is the internal state we drive.
	// Including it would create races between user toggles and animation timers.
	useEffect(() => {
		if (open) {
			if (animationState === 'closed') setAnimationState('entering');
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
				elementToFocus?.focus();
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
			if (animationState !== 'open') return;

			if (e.key === 'Escape' && !preventClose) {
				e.preventDefault();
				onClose();
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
		if (!preventClose && animationState === 'open') {
			onClose();
		}
	}, [preventClose, onClose, animationState]);

	if (!mounted || animationState === 'closed') return null;

	const isVisible = animationState === 'open';

	return createPortal(
		<div className="fixed inset-0 z-50" style={{ overscrollBehavior: 'contain' }}>
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
			/>

			<div className={cn('fixed max-w-full', styles.container)}>
				<div
					ref={sheetRef}
					role="dialog"
					aria-modal="true"
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
					data-testid="sheet-primitive"
				>
					{children}
				</div>
			</div>
		</div>,
		document.body,
	);
}
