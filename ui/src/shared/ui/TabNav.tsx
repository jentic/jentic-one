import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';

/**
 * TabNav — underline-style page tabs for detail views (Overview / Activity /
 * Settings…). Visually distinct from `SegmentedToggle` on purpose:
 *
 *   - `SegmentedToggle` is a compact *value picker* (filters, chart lenses) —
 *     a bordered pill group that reads as one control.
 *   - `TabNav` is *page navigation*: quiet text tabs on a full-width hairline,
 *     with an animated underline under the active tab and optional per-tab
 *     icon + count badge.
 *
 * Implements the WAI-ARIA tabs pattern with automatic activation: roving
 * tabIndex plus Left/Right/Home/End moving focus AND selection. The underline
 * is an absolutely-positioned bar measured from the active button (same
 * scroll-safe technique as `SegmentedToggle`; see framer/motion#1535 for why
 * `layoutId` is avoided). It glides on transforms alone — a 1px bar moved with
 * `x` and stretched with `scaleX` from its left edge — on the app's
 * ease-out-soft curve with no overshoot, so it never forces a layout mid-glide.
 */

/** The underline's glide: `--ease-out-soft`, the curve the app's motion shares. */
const INDICATOR_TRANSITION = { duration: 0.24, ease: [0.22, 1, 0.36, 1] } as const;

export interface TabNavOption<T extends string = string> {
	value: T;
	label: string;
	/** Optional leading glyph, sized by the caller (`h-4 w-4`). */
	icon?: ReactNode;
	/** Optional trailing count badge. Hidden when undefined. */
	count?: number;
}

/** How a tab was chosen: `click` is a click (or Enter/Space on the tab), which
 * a host may follow by moving focus into the panel; `arrow` is Left/Right/
 * Home/End, where focus stays on the tab list so the next arrow keeps moving. */
export type TabNavChangeSource = 'click' | 'arrow';

interface TabNavProps<T extends string = string> {
	options: TabNavOption<T>[];
	value: T;
	onChange: (value: T, source: TabNavChangeSource) => void;
	/** Accessible name for the tablist. */
	ariaLabel: string;
	/** Map an option value → the `id` to give its tab button. */
	getTabId?: (value: T) => string | undefined;
	/** Map an option value → the `id` of the tabpanel it controls. */
	getControls?: (value: T) => string | undefined;
	/** The tabs share the bar's width equally, each label centred in its share
	 * (a panel's two routes in, say). Off, each tab is as wide as its label. */
	fill?: boolean;
	className?: string;
}

interface IndicatorRect {
	left: number;
	width: number;
}

export function TabNav<T extends string = string>({
	options,
	value,
	onChange,
	ariaLabel,
	getTabId,
	getControls,
	fill = false,
	className,
}: TabNavProps<T>) {
	const containerRef = useRef<HTMLDivElement>(null);
	const btnRefs = useRef(new Map<string, HTMLButtonElement>());
	const [indicator, setIndicator] = useState<IndicatorRect | null>(null);

	useLayoutEffect(() => {
		function measure() {
			const btn = btnRefs.current.get(value);
			if (!containerRef.current || !btn) return;
			setIndicator({ left: btn.offsetLeft, width: btn.offsetWidth });
		}
		measure();
		const ro = new ResizeObserver(measure);
		if (containerRef.current) ro.observe(containerRef.current);
		return () => ro.disconnect();
	}, [value, options]);

	function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
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
		onChange(next, 'arrow');
		btnRefs.current.get(next)?.focus();
	}

	return (
		<div
			ref={containerRef}
			role="tablist"
			aria-label={ariaLabel}
			className={cn(
				'border-border relative flex max-w-full overflow-x-auto overflow-y-hidden border-b',
				fill ? 'w-full items-stretch' : 'items-end gap-1',
				className,
			)}
		>
			{indicator && (
				<motion.div
					aria-hidden="true"
					data-testid="tabnav-indicator"
					className="bg-primary pointer-events-none absolute bottom-0 left-0 h-0.5 w-px will-change-transform"
					style={{ originX: 0 }}
					initial={false}
					animate={{ x: indicator.left, scaleX: indicator.width }}
					transition={INDICATOR_TRANSITION}
				/>
			)}
			{options.map((option) => {
				const isActive = value === option.value;
				return (
					<button
						key={option.value}
						type="button"
						role="tab"
						id={getTabId?.(option.value)}
						aria-selected={isActive}
						aria-controls={getControls?.(option.value)}
						tabIndex={isActive ? 0 : -1}
						ref={(el) => {
							if (el) btnRefs.current.set(option.value, el);
							else btnRefs.current.delete(option.value);
						}}
						onClick={() => onChange(option.value, 'click')}
						onKeyDown={handleKeyDown}
						className={cn(
							'focus-visible:ring-ring group relative inline-flex cursor-pointer items-center gap-1.5 rounded-t-md px-3 py-2.5 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none',
							fill ? 'min-w-0 flex-1 basis-0 justify-center' : 'shrink-0',
							isActive
								? 'text-foreground'
								: 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
						)}
					>
						{option.icon && (
							<span
								aria-hidden="true"
								className={cn(
									'transition-colors',
									isActive
										? 'text-primary'
										: 'text-muted-foreground/70 group-hover:text-foreground',
								)}
							>
								{option.icon}
							</span>
						)}
						{option.label}
						{option.count != null && (
							<span
								className={cn(
									'rounded-full px-1.5 py-0.5 font-mono text-[10px] leading-none transition-colors',
									isActive
										? 'bg-primary/15 text-primary'
										: 'bg-muted text-muted-foreground',
								)}
							>
								{option.count}
							</span>
						)}
					</button>
				);
			})}
		</div>
	);
}
