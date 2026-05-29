import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';

interface HoverTooltipProps {
	/** The trigger element. The tooltip opens on hover/focus over this node. */
	children: ReactNode;
	/** The tooltip body. Plain string or arbitrary JSX. */
	content: ReactNode;
	/**
	 * If true, the tooltip closes the moment the mouse leaves the trigger
	 * (no "bridge" zone). If false (default), users can move the mouse
	 * onto the tooltip to keep it open — useful when content has links.
	 */
	closeOnTooltipHover?: boolean;
	/** Wrapper class — useful for flex layouts (`flex-1`, `min-w-0`, etc.). */
	className?: string;
	/**
	 * Class on the trigger itself (the element that gets the mouse handlers).
	 * Default `inline-flex` keeps the trigger inline so badges/buttons sit
	 * naturally next to siblings.
	 */
	triggerClassName?: string;
	/**
	 * Where to place the tooltip relative to the trigger. Default `top`.
	 * The tooltip clamps to the viewport so it never paints off-screen.
	 */
	side?: 'top' | 'bottom' | 'left' | 'right';
	/** Distance in px between the trigger edge and the tooltip. Default 8. */
	gap?: number;
	/**
	 * Optional `role`. Defaults to `tooltip` (most cases). When the trigger
	 * is interactive (a button), screen readers expose the linked content
	 * via `aria-describedby` automatically.
	 */
	role?: 'tooltip' | 'status';
}

/**
 * Hover-activated tooltip rendered through a React portal so it escapes
 * any `overflow:hidden` ancestor.
 *
 * API mirrors `@jentic-frontend-ui/HoverTooltip` (children/content/
 * closeOnTooltipHover/className/triggerClassName) so a future swap to
 * the published component is an import-path change. Built on the same
 * `createPortal` + fixed-position pattern as `TruncateWithTooltip` —
 * no new deps.
 */
export function HoverTooltip({
	children,
	content,
	closeOnTooltipHover = false,
	className,
	triggerClassName = 'inline-flex',
	side = 'top',
	gap = 8,
	role = 'tooltip',
}: HoverTooltipProps) {
	const triggerRef = useRef<HTMLSpanElement>(null);
	const overTrigger = useRef(false);
	const overPanel = useRef(false);
	const tooltipId = useRef(`tt-${Math.random().toString(36).slice(2, 9)}`);
	const [open, setOpen] = useState(false);
	const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

	const computePos = useCallback(() => {
		const el = triggerRef.current;
		if (!el) return null;
		const rect = el.getBoundingClientRect();
		switch (side) {
			case 'bottom':
				return { top: rect.bottom + gap, left: rect.left + rect.width / 2 };
			case 'left':
				return { top: rect.top + rect.height / 2, left: rect.left - gap };
			case 'right':
				return { top: rect.top + rect.height / 2, left: rect.right + gap };
			case 'top':
			default:
				return { top: rect.top - gap, left: rect.left + rect.width / 2 };
		}
	}, [side, gap]);

	const checkClose = useCallback(() => {
		requestAnimationFrame(() => {
			if (!overTrigger.current && !overPanel.current) {
				setOpen(false);
			}
		});
	}, []);

	const handleTriggerEnter = useCallback(() => {
		overTrigger.current = true;
		const p = computePos();
		if (p) setPos(p);
		setOpen(true);
	}, [computePos]);

	const handleTriggerLeave = useCallback(() => {
		overTrigger.current = false;
		if (closeOnTooltipHover) {
			setOpen(false);
		} else {
			checkClose();
		}
	}, [closeOnTooltipHover, checkClose]);

	const handlePanelEnter = useCallback(() => {
		if (!closeOnTooltipHover) overPanel.current = true;
	}, [closeOnTooltipHover]);

	const handlePanelLeave = useCallback(() => {
		overPanel.current = false;
		checkClose();
	}, [checkClose]);

	useEffect(() => {
		if (!open) return;
		const onScrollOrResize = () => {
			const p = computePos();
			if (p) setPos(p);
		};
		window.addEventListener('scroll', onScrollOrResize, true);
		window.addEventListener('resize', onScrollOrResize);
		return () => {
			window.removeEventListener('scroll', onScrollOrResize, true);
			window.removeEventListener('resize', onScrollOrResize);
		};
	}, [open, computePos]);

	const transform =
		side === 'top'
			? 'translate(-50%, -100%)'
			: side === 'bottom'
				? 'translate(-50%, 0)'
				: side === 'left'
					? 'translate(-100%, -50%)'
					: 'translate(0, -50%)';

	return (
		<span
			ref={triggerRef}
			className={cn(triggerClassName, className)}
			onMouseEnter={handleTriggerEnter}
			onMouseLeave={handleTriggerLeave}
			onFocus={handleTriggerEnter}
			onBlur={handleTriggerLeave}
			aria-describedby={open ? tooltipId.current : undefined}
		>
			{children}
			{open &&
				pos &&
				createPortal(
					<span
						id={tooltipId.current}
						role={role}
						className="bg-popover/95 text-popover-foreground border-border pointer-events-auto fixed z-[9999] max-w-[320px] rounded-md border px-2.5 py-1.5 text-xs whitespace-normal shadow-lg backdrop-blur-sm"
						style={{ top: pos.top, left: pos.left, transform }}
						onMouseEnter={handlePanelEnter}
						onMouseLeave={handlePanelLeave}
					>
						{content}
					</span>,
					document.body,
				)}
		</span>
	);
}
