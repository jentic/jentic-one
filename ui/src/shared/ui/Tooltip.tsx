import {
	cloneElement,
	isValidElement,
	useCallback,
	useEffect,
	useId,
	useRef,
	useState,
	type ReactElement,
	type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/shared/lib/utils';

type TooltipPlacement = 'top' | 'bottom';

interface TooltipProps {
	/** Tooltip content shown on hover/focus. Rendered into a body portal. */
	content: ReactNode;
	/** The trigger content the tooltip describes. */
	children: ReactNode;
	/** Where the tooltip sits relative to the trigger. Default `top`. */
	placement?: TooltipPlacement;
	/**
	 * Hover open delay in ms (default 400). Keyboard focus always opens
	 * immediately so assistive-tech users aren't gated on a timer.
	 */
	delayMs?: number;
	/** Extra classes for the inline trigger wrapper. */
	className?: string;
	/** Extra classes for the tooltip bubble. */
	bubbleClassName?: string;
	/**
	 * Set when the child is itself focusable (e.g. a `<button>`). The wrapper then
	 * drops its own `tabIndex` (so the control isn't a double tab stop) AND its
	 * `aria-describedby`; the association is cloned onto the focusable child
	 * instead, since `aria-describedby` is not inherited from the wrapper. The
	 * child must be a single valid React element in this mode.
	 */
	interactiveChild?: boolean;
}

/**
 * A lightweight, reusable hover/focus tooltip.
 *
 * Unlike the native `title` attribute (which has a ~1s browser delay and
 * unstyled chrome) this shows **instantly** on pointer-enter / focus and is
 * consistently styled. Unlike `TruncateWithTooltip` it always shows its
 * `content` (a value distinct from the trigger) rather than gating on overflow.
 *
 * The trigger is wrapped in a focusable inline `<span>`; the bubble renders into
 * a `document.body` portal so it escapes any `overflow-hidden` ancestor (e.g.
 * the rail's scroll container), and carries `role="tooltip"` +
 * `aria-describedby` for assistive tech.
 */
export function Tooltip({
	content,
	children,
	placement = 'top',
	delayMs = 400,
	className,
	bubbleClassName,
	interactiveChild = false,
}: TooltipProps) {
	const triggerRef = useRef<HTMLSpanElement>(null);
	const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const [show, setShow] = useState(false);
	const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
	const tooltipId = useId();

	const clearTimer = useCallback(() => {
		if (timerRef.current) {
			clearTimeout(timerRef.current);
			timerRef.current = null;
		}
	}, []);

	/** Measure the trigger and place the bubble relative to it. */
	const measure = useCallback(() => {
		const el = triggerRef.current;
		if (!el) return;
		const rect = el.getBoundingClientRect();
		const top = placement === 'top' ? rect.top - 8 : rect.bottom + 8;
		const left = rect.left + rect.width / 2;
		// Skip the state write (and its re-render) when the measured position
		// hasn't moved — a nested scroll that doesn't shift the trigger shouldn't
		// churn React.
		setPos((prev) => (prev && prev.top === top && prev.left === left ? prev : { top, left }));
	}, [placement]);

	/** Position + reveal the bubble now. */
	const reveal = useCallback(() => {
		if (!triggerRef.current) return;
		measure();
		setShow(true);
	}, [measure]);

	/** Hover open — gated behind `delayMs` so a quick pass-over doesn't flash. */
	const openDelayed = useCallback(() => {
		clearTimer();
		if (delayMs <= 0) {
			reveal();
			return;
		}
		timerRef.current = setTimeout(reveal, delayMs);
	}, [clearTimer, delayMs, reveal]);

	const close = useCallback(() => {
		clearTimer();
		setShow(false);
	}, [clearTimer]);

	// Never leak a pending open timer if the trigger unmounts mid-delay.
	useEffect(() => clearTimer, [clearTimer]);

	// While open, keep the fixed-position bubble pinned to the trigger. Inside
	// the rail's `overflow-y-auto` feed an auto-scroll would otherwise strand the
	// bubble at its reveal coordinates. Listen on the capture phase so ancestor
	// scroll containers (not just window) are caught. Capture-phase scroll fires
	// for every nested scroller, so coalesce bursts into one measurement per
	// animation frame rather than a synchronous rect+setState per event.
	useEffect(() => {
		if (!show) return undefined;
		let rafId: number | null = null;
		const onReflow = () => {
			if (rafId !== null) return;
			rafId = window.requestAnimationFrame(() => {
				rafId = null;
				measure();
			});
		};
		window.addEventListener('scroll', onReflow, true);
		window.addEventListener('resize', onReflow);
		return () => {
			if (rafId !== null) window.cancelAnimationFrame(rafId);
			window.removeEventListener('scroll', onReflow, true);
			window.removeEventListener('resize', onReflow);
		};
	}, [show, measure]);

	// When the child is itself focusable, `aria-describedby` is NOT inherited
	// from the wrapper — focus lands on the inner control, so the association
	// has to live ON that control. Clone it onto the child and leave the wrapper
	// non-focusable and un-described. Otherwise the wrapper is the focus target
	// and carries the association itself.
	const describedChildren =
		interactiveChild && isValidElement(children)
			? cloneElement(children as ReactElement<{ 'aria-describedby'?: string }>, {
					'aria-describedby': tooltipId,
				})
			: children;

	return (
		<span
			ref={triggerRef}
			className={cn('inline-flex', className)}
			tabIndex={interactiveChild ? undefined : 0}
			aria-describedby={interactiveChild ? undefined : tooltipId}
			onMouseEnter={openDelayed}
			onMouseLeave={close}
			onFocus={reveal}
			onBlur={close}
		>
			{describedChildren}
			{/* The described text node is rendered UNCONDITIONALLY (visually hidden
			    when closed) so the `aria-describedby` association is stable across
			    renders and screen readers pick it up at focus time. Only the styled,
			    positioned bubble is gated on `show`. */}
			{show && pos ? (
				createPortal(
					<span
						id={tooltipId}
						role="tooltip"
						className={cn(
							'border-border/40 bg-card/70 text-card-foreground pointer-events-none fixed z-[9999] max-w-[320px] -translate-x-1/2 rounded-lg border px-3 py-2 text-xs whitespace-nowrap shadow-xl backdrop-blur-md',
							placement === 'top' && '-translate-y-full',
							bubbleClassName,
						)}
						style={{ top: pos.top, left: pos.left }}
					>
						{content}
					</span>,
					document.body,
				)
			) : (
				<span
					id={tooltipId}
					style={{
						position: 'absolute',
						width: 1,
						height: 1,
						padding: 0,
						margin: -1,
						overflow: 'hidden',
						clip: 'rect(0, 0, 0, 0)',
						whiteSpace: 'nowrap',
						borderWidth: 0,
					}}
				>
					{content}
				</span>
			)}
		</span>
	);
}
