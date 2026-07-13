import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';

/**
 * Faithful port of `SegmentedToggle` from the design system. A row of
 * touching segments with a soft pill that slides between the active one.
 *
 * The pill is a single, always-mounted absolutely-positioned element whose
 * `left`/`width` are measured from the active button and animated with a
 * spring. We deliberately do NOT use framer-motion's shared-element
 * `layoutId` here: that measures the start position relative to the
 * document and ignores page scroll, so clicking a segment after scrolling
 * made the pill fly in from the bottom of the viewport (framer/motion#1535).
 * Measuring offsets within the toggle itself is scroll-independent.
 */

export interface SegmentedToggleOption<T extends string = string> {
	value: T;
	label: string;
}

interface SegmentedToggleProps<T extends string = string> {
	options: SegmentedToggleOption<T>[];
	value: T;
	onChange: (value: T) => void;
	/**
	 * Kept for API compatibility with existing call sites. No longer used for
	 * the animation (the pill is measured locally, not shared via `layoutId`),
	 * but harmless to pass.
	 */
	layoutId?: string;
	className?: string;
	/**
	 * Semantic role of the control. `'tabs'` opts into full ARIA tab semantics
	 * (`role=tablist/tab`, `aria-selected`, roving `tabIndex`, and Left/Right/
	 * Home/End arrow-key navigation). Defaults to `'toggle'` — a plain group of
	 * buttons — to preserve behaviour for non-tab call sites (filters, etc.).
	 */
	as?: 'toggle' | 'tabs';
	/**
	 * Accessible name for the group. Used as `aria-label` on the tablist when
	 * `as='tabs'`.
	 */
	ariaLabel?: string;
	/**
	 * Map an option value → the `id` of the tabpanel it controls (for
	 * `aria-controls`/`id` wiring). Only used when `as='tabs'`.
	 */
	getControls?: (value: T) => string | undefined;
	/** Map an option value → the `id` to give its tab button. Only for `as='tabs'`. */
	getTabId?: (value: T) => string | undefined;
}

interface PillRect {
	left: number;
	width: number;
}

export function SegmentedToggle<T extends string = string>({
	options,
	value,
	onChange,
	className,
	as = 'toggle',
	ariaLabel,
	getControls,
	getTabId,
}: SegmentedToggleProps<T>) {
	const containerRef = useRef<HTMLDivElement>(null);
	const btnRefs = useRef(new Map<string, HTMLButtonElement>());
	const [pill, setPill] = useState<PillRect | null>(null);
	const isTabs = as === 'tabs';

	// Measure the active button's box relative to the container after layout,
	// and re-measure on resize. `useLayoutEffect` so the pill is positioned
	// before paint (no first-frame flash at 0,0).
	useLayoutEffect(() => {
		function measure() {
			const container = containerRef.current;
			const btn = btnRefs.current.get(value);
			if (!container || !btn) return;
			setPill({ left: btn.offsetLeft, width: btn.offsetWidth });
		}
		measure();
		const ro = new ResizeObserver(measure);
		if (containerRef.current) ro.observe(containerRef.current);
		return () => ro.disconnect();
	}, [value, options]);

	// Roving Left/Right/Home/End navigation for tab semantics. Moving focus also
	// activates the tab (automatic activation), matching the WAI-ARIA tabs pattern
	// for a small, cheap-to-render lens switcher.
	function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
		if (!isTabs) return;
		const idx = options.findIndex((o) => o.value === value);
		if (idx === -1) return;
		let nextIdx: number | null = null;
		if (e.key === 'ArrowRight' || e.key === 'ArrowDown') nextIdx = (idx + 1) % options.length;
		else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp')
			nextIdx = (idx - 1 + options.length) % options.length;
		else if (e.key === 'Home') nextIdx = 0;
		else if (e.key === 'End') nextIdx = options.length - 1;
		if (nextIdx == null) return;
		e.preventDefault();
		const next = options[nextIdx].value;
		onChange(next);
		btnRefs.current.get(next)?.focus();
	}

	return (
		<div
			ref={containerRef}
			role={isTabs ? 'tablist' : undefined}
			aria-label={isTabs ? ariaLabel : undefined}
			className={cn(
				'border-border bg-muted/50 relative flex rounded-lg border p-0.5',
				className,
			)}
		>
			{pill && (
				<motion.div
					aria-hidden="true"
					className="bg-foreground/10 ring-border/50 pointer-events-none absolute top-0.5 bottom-0.5 rounded-md shadow-sm ring-1"
					initial={false}
					animate={{ left: pill.left, width: pill.width }}
					transition={{ type: 'spring', stiffness: 500, damping: 35 }}
				/>
			)}
			{options.map((option) => {
				const isActive = value === option.value;
				return (
					<button
						key={option.value}
						type="button"
						role={isTabs ? 'tab' : undefined}
						id={isTabs ? getTabId?.(option.value) : undefined}
						aria-selected={isTabs ? isActive : undefined}
						aria-controls={isTabs ? getControls?.(option.value) : undefined}
						tabIndex={isTabs ? (isActive ? 0 : -1) : undefined}
						ref={(el) => {
							if (el) btnRefs.current.set(option.value, el);
							else btnRefs.current.delete(option.value);
						}}
						onClick={() => onChange(option.value)}
						onKeyDown={handleKeyDown}
						className={cn(
							'relative rounded-md px-3 py-1 text-xs font-medium transition-colors',
							!isActive && 'cursor-pointer',
						)}
					>
						<span
							className={cn(
								'relative z-10 transition-colors',
								isActive
									? 'text-foreground'
									: 'text-muted-foreground hover:text-foreground',
							)}
						>
							{option.label}
						</span>
					</button>
				);
			})}
		</div>
	);
}
