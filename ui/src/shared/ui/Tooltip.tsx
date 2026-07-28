import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
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

	/** Position + reveal the bubble now. */
	const reveal = useCallback(() => {
		const el = triggerRef.current;
		if (!el) return;
		const rect = el.getBoundingClientRect();
		const top = placement === 'top' ? rect.top - 8 : rect.bottom + 8;
		setPos({ top, left: rect.left + rect.width / 2 });
		setShow(true);
	}, [placement]);

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

	return (
		<span
			ref={triggerRef}
			className={cn('inline-flex', className)}
			tabIndex={0}
			aria-describedby={show ? tooltipId : undefined}
			onMouseEnter={openDelayed}
			onMouseLeave={close}
			onFocus={reveal}
			onBlur={close}
		>
			{children}
			{show &&
				pos &&
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
				)}
		</span>
	);
}
