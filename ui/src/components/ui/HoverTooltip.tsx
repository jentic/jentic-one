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
	/**
	 * Tab index for the trigger wrapper. Defaults to `0` so keyboard and
	 * screen-reader users can focus the trigger and surface the tooltip
	 * (parity with the mouse path). Set to `-1` when the trigger already
	 * wraps a natively focusable element (a `<button>`/`<a>`) to avoid a
	 * redundant double tab-stop — focus on the inner element still opens
	 * the tooltip via the bubbled `onFocus`.
	 */
	tabIndex?: number;
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
	tabIndex = 0,
}: HoverTooltipProps) {
	const triggerRef = useRef<HTMLSpanElement>(null);
	const panelRef = useRef<HTMLSpanElement>(null);
	const overTrigger = useRef(false);
	const overPanel = useRef(false);
	const tooltipId = useRef(`tt-${Math.random().toString(36).slice(2, 9)}`);
	const [open, setOpen] = useState(false);
	const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
	// Whether the current `pos` has been measured against the viewport.
	// Reset to false on every reposition so the panel never flashes
	// off-screen for one frame before the clamp effect runs.
	const [clamped, setClamped] = useState(false);

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

	// After the panel mounts (or content/position changes) measure it and
	// clamp the centered/anchored position so the panel stays fully inside
	// the viewport. Without this the trigger near a viewport edge produces
	// a panel whose `max-w` is honoured but whose content gets squeezed
	// into the narrow strip of remaining space, rendering as a
	// near-vertical column of one-word lines.
	const clampToViewport = useCallback(() => {
		const panel = panelRef.current;
		if (!panel || !pos) return;
		const margin = 8;
		const w = panel.offsetWidth;
		const h = panel.offsetHeight;
		const vw = window.innerWidth;
		const vh = window.innerHeight;

		let left = pos.left;
		let top = pos.top;

		// Resolve the centered/anchored position into the panel's actual
		// rendered rect, then push it back inside the viewport if it
		// overflows on either axis.
		if (side === 'top' || side === 'bottom') {
			const half = w / 2;
			if (left - half < margin) left = half + margin;
			else if (left + half > vw - margin) left = vw - margin - half;
			if (side === 'top' && top - h < margin) top = h + margin;
			else if (side === 'bottom' && top + h > vh - margin) top = vh - margin - h;
		} else {
			const half = h / 2;
			if (top - half < margin) top = half + margin;
			else if (top + half > vh - margin) top = vh - margin - half;
			if (side === 'left' && left - w < margin) left = w + margin;
			else if (side === 'right' && left + w > vw - margin) left = vw - margin - w;
		}

		if (left !== pos.left || top !== pos.top) setPos({ top, left });
		setClamped(true);
	}, [pos, side]);

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
		if (p) {
			setPos(p);
			setClamped(false);
		}
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
			if (p) {
				setPos(p);
				setClamped(false);
			}
		};
		// Escape dismisses the tooltip — keyboard users who focused the
		// trigger (or moused over content with links) need an explicit way
		// out that doesn't require moving the pointer.
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key === 'Escape') {
				overTrigger.current = false;
				overPanel.current = false;
				setOpen(false);
			}
		};
		window.addEventListener('scroll', onScrollOrResize, true);
		window.addEventListener('resize', onScrollOrResize);
		window.addEventListener('keydown', onKeyDown);
		return () => {
			window.removeEventListener('scroll', onScrollOrResize, true);
			window.removeEventListener('resize', onScrollOrResize);
			window.removeEventListener('keydown', onKeyDown);
		};
	}, [open, computePos]);

	// Clamp after every position change. Runs once per `pos` update, and
	// the clamp itself only setPos when something would overflow, so the
	// effect converges in at most two paints.
	useEffect(() => {
		if (!open) return;
		clampToViewport();
	}, [open, pos, clampToViewport]);

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
			tabIndex={tabIndex}
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
						ref={panelRef}
						id={tooltipId.current}
						role={role}
						className="bg-popover/95 text-popover-foreground border-border pointer-events-auto fixed z-[9999] inline-block max-w-[320px] min-w-[160px] rounded-md border px-2.5 py-1.5 text-xs whitespace-normal shadow-lg backdrop-blur-sm"
						style={{
							top: pos.top,
							left: pos.left,
							transform,
							visibility: clamped ? 'visible' : 'hidden',
						}}
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
