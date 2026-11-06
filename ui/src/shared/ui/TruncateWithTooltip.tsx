import {
	useRef,
	useState,
	useEffect,
	useCallback,
	useId,
	type ReactNode,
	type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/shared/lib/utils';

/**
 * Whether the element's single line is cut off (`scrollWidth > clientWidth`),
 * re-measured whenever it resizes and whenever `content` changes. For a
 * caller that shows the full text its own way (e.g. folds it into a tooltip
 * the element already has) instead of through `TruncateWithTooltip`.
 */
function useIsTruncated(ref: RefObject<HTMLElement | null>, content: unknown): boolean {
	const [truncated, setTruncated] = useState(false);
	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		const check = () => setTruncated(el.scrollWidth > el.clientWidth);
		check();
		if (typeof ResizeObserver === 'undefined') {
			// No observer to catch post-layout size: the mount-time measurement can
			// read 0 before layout settles, so re-check once on the next frame.
			const raf = requestAnimationFrame(check);
			return () => cancelAnimationFrame(raf);
		}
		const ro = new ResizeObserver(check);
		ro.observe(el);
		return () => ro.disconnect();
	}, [ref, content]);
	return truncated;
}

interface TruncateWithTooltipProps {
	children: ReactNode;
	className?: string;
	/**
	 * Sit inside running text — `inline-block`, bottom-aligned with the line —
	 * instead of as its own block line. Give it a width budget (`max-w-[24ch]`).
	 */
	inline?: boolean;
	/**
	 * Leave it out of the tab order even when cut off: for text inside a control
	 * (a button, a tab) whose accessible name already carries the full text,
	 * where a focusable span would nest one interactive element in another.
	 * Hover still shows the tooltip.
	 */
	focusable?: boolean;
	/** The tooltip's text, when it isn't the children themselves. */
	tooltip?: ReactNode;
	/** `auto` isolates user-chosen text as its own bidi run. */
	dir?: 'auto' | 'ltr' | 'rtl';
}

/**
 * Renders children in a single truncated line. When the content overflows,
 * hovering (or focusing via keyboard) shows a fixed-position tooltip with the
 * full text that escapes any overflow-hidden ancestors (e.g. the detail
 * sheet's scroll container). The trigger only becomes focusable when it
 * actually overflows, so non-truncated cells stay out of the tab order — and
 * text that fits never gets a tooltip at all. Escape dismisses it.
 */
export function TruncateWithTooltip({
	children,
	className,
	inline = false,
	focusable = true,
	tooltip,
	dir,
}: TruncateWithTooltipProps) {
	const ref = useRef<HTMLSpanElement>(null);
	const overflows = useIsTruncated(ref, children);
	const [show, setShow] = useState(false);
	const [pos, setPos] = useState<{ top: number; left: number; host: Element } | null>(null);
	const tooltipId = useId();

	/** Place the bubble under the trigger, where the trigger is now. */
	const place = useCallback(() => {
		const el = ref.current;
		if (!el) return;
		const rect = el.getBoundingClientRect();
		// Keep the 320px bubble on screen near the right edge.
		const left = Math.max(8, Math.min(rect.left, window.innerWidth - 328));
		const top = rect.bottom + 4;
		// Inside a modal `<dialog>` the bubble must join it in the top layer, or
		// it renders beneath the dialog and its backdrop. An unmoved trigger
		// keeps the same state, so a scroll that doesn't shift it re-renders nothing.
		setPos((prev) =>
			prev && prev.top === top && prev.left === left
				? prev
				: { top, left, host: el.closest('dialog') ?? document.body },
		);
	}, []);

	const open = useCallback(() => {
		if (!overflows || !ref.current) return;
		place();
		setShow(true);
	}, [overflows, place]);

	const close = useCallback(() => {
		setShow(false);
	}, []);

	// While open, the fixed-position bubble follows its trigger: a scroll of
	// any ancestor (the detail sheet's own scroller, not just the window — so
	// the capture phase) or a resize re-places it, one measurement per frame.
	// Escape dismisses it (WCAG 1.4.13), as the shared Tooltip does.
	useEffect(() => {
		if (!show) return undefined;
		let raf: number | null = null;
		const onReflow = () => {
			if (raf !== null) return;
			raf = window.requestAnimationFrame(() => {
				raf = null;
				place();
			});
		};
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key === 'Escape') close();
		};
		window.addEventListener('scroll', onReflow, true);
		window.addEventListener('resize', onReflow);
		document.addEventListener('keydown', onKeyDown);
		return () => {
			if (raf !== null) window.cancelAnimationFrame(raf);
			window.removeEventListener('scroll', onReflow, true);
			window.removeEventListener('resize', onReflow);
			document.removeEventListener('keydown', onKeyDown);
		};
	}, [show, place, close]);

	return (
		<span
			ref={ref}
			dir={dir}
			className={cn(
				inline ? 'inline-block max-w-full truncate align-bottom' : 'block truncate',
				className,
			)}
			tabIndex={overflows && focusable ? 0 : undefined}
			aria-describedby={show ? tooltipId : undefined}
			onMouseEnter={open}
			onMouseLeave={close}
			// Keyboard focus only: focus a sheet moves here after a click is not
			// `:focus-visible`, and must not open the bubble over it.
			onFocus={(e) => {
				if (e.currentTarget.matches(':focus-visible')) open();
			}}
			onBlur={close}
		>
			{children}
			{show &&
				pos &&
				createPortal(
					<span
						id={tooltipId}
						role="tooltip"
						// The shared Tooltip's bubble; `overflow-wrap:anywhere` so a long
						// name with no spaces still wraps inside it.
						className="border-border/40 bg-card/70 text-card-foreground pointer-events-none fixed z-[9999] max-w-[320px] rounded-lg border px-3 py-2 text-xs font-normal tracking-normal [overflow-wrap:anywhere] whitespace-normal normal-case no-underline shadow-xl backdrop-blur-md"
						style={{ top: pos.top, left: pos.left }}
					>
						{tooltip ?? children}
					</span>,
					pos.host,
				)}
		</span>
	);
}
