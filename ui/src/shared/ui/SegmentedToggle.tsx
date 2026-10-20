import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
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
	/** Optional leading glyph (decorative — the label stays the accessible name). */
	icon?: ReactNode;
	/**
	 * Classes for this option while it is the active one — its pill fill and
	 * label colour (e.g. a caution tint for an "Ask" choice). Absent → the
	 * tone's neutral pill.
	 */
	activeTint?: { pill: string; text: string };
}

/**
 * Where the control sits (all borderless; the active segment is a lighter
 * tonal pill, no ring or shadow): `default` takes one step lighter than its
 * container (`--field-bg`); `surface` is `surface-1` on the page background;
 * `inset` is one step lighter than a `surface-1` panel (smaller labels).
 */
export type SegmentedToggleTone = 'default' | 'surface' | 'inset';

/** `xs` fits inside a list row (a per-row choice); `default` is a toolbar control. */
export type SegmentedToggleSize = 'default' | 'xs';

/** The `xs` size's overrides on any tone: a slimmer frame and segments. */
const XS_STYLES = {
	root: 'gap-px p-[2px]',
	pill: 'top-[2px] bottom-[2px] rounded-[5px]',
	segment: 'rounded-[5px] px-2 py-0.5 text-[11px] font-semibold',
};

const TONE_STYLES: Record<
	SegmentedToggleTone,
	{ root: string; pill: string; segment: string; active: string; idle: string }
> = {
	default: {
		root: 'bg-field rounded-field gap-[2px] border-0 p-[3px]',
		pill: 'bg-surface-tonal-hover top-[3px] bottom-[3px] rounded-[6px]',
		segment: 'rounded-[6px] px-3 py-1 text-xs font-semibold',
		active: 'text-foreground',
		idle: 'text-foreground-sub',
	},
	surface: {
		root: 'bg-surface-1 rounded-field gap-[2px] border-0 p-[3px]',
		pill: 'bg-surface-tonal-hover top-[3px] bottom-[3px] rounded-[6px]',
		segment: 'rounded-[6px] px-3 py-[5px] text-xs font-semibold',
		active: 'text-foreground',
		idle: 'text-foreground-sub',
	},
	inset: {
		root: 'bg-surface-field rounded-field gap-[2px] border-0 p-[3px]',
		pill: 'bg-surface-tonal-hover top-[3px] bottom-[3px] rounded-[6px]',
		segment: 'rounded-[6px] px-2.5 py-1 text-xs font-semibold',
		active: 'text-foreground',
		idle: 'text-foreground-sub',
	},
};

interface SegmentedToggleProps<T extends string = string> {
	options: SegmentedToggleOption<T>[];
	/** The pressed option; `null` = none (only meaningful with `onClear`). */
	value: T | null;
	onChange: (value: T) => void;
	/**
	 * Lets the choice be emptied: pressing the pressed option, or Delete /
	 * Backspace in `'choice'` mode, calls this instead of `onChange`.
	 */
	onClear?: () => void;
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
	 * Home/End arrow-key navigation). `'choice'` is a pick-one-or-none field
	 * inside a list row: a named `role=group` of `aria-pressed` buttons with ONE
	 * tab stop (roving `tabIndex`). Arrow keys and Home/End move focus only;
	 * Space/Enter press. Pressing the pressed option (or Delete/Backspace) calls
	 * `onClear`, so "none" is always reachable — which a radio group can't do.
	 * Defaults to `'toggle'` — a plain group of buttons — to preserve behaviour
	 * for non-tab call sites (filters, etc.).
	 */
	as?: 'toggle' | 'tabs' | 'choice';
	/**
	 * Accessible name for the group. Applied as `aria-label` on the tablist
	 * when `as='tabs'`, or on a `role="group"` wrapper in toggle mode.
	 */
	ariaLabel?: string;
	/**
	 * Map an option value → the `id` of the tabpanel it controls (for
	 * `aria-controls`/`id` wiring). Only used when `as='tabs'`.
	 */
	getControls?: (value: T) => string | undefined;
	/** Map an option value → the `id` to give its tab button. Only for `as='tabs'`. */
	getTabId?: (value: T) => string | undefined;
	/**
	 * Nothing to choose between (e.g. a filter over an empty list): every
	 * segment is `disabled` and the group is `aria-disabled`.
	 */
	disabled?: boolean;
	/** Surface the control sits on (default: one step lighter than its container). */
	tone?: SegmentedToggleTone;
	/** `xs` for a choice inside a list row. Default `default`. */
	size?: SegmentedToggleSize;
	/**
	 * The control acts as a form field (a choice inside a form, not a
	 * toolbar/panel filter): draws the inputs' faint resting edge.
	 */
	field?: boolean;
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
	disabled = false,
	tone = 'default',
	size = 'default',
	field = false,
	onClear,
}: SegmentedToggleProps<T>) {
	const toneStyles = TONE_STYLES[tone];
	const styles =
		size === 'xs'
			? {
					...toneStyles,
					root: cn(toneStyles.root, XS_STYLES.root),
					pill: cn(toneStyles.pill, XS_STYLES.pill),
					segment: XS_STYLES.segment,
				}
			: toneStyles;
	const activeOption = options.find((o) => o.value === value);
	const containerRef = useRef<HTMLDivElement>(null);
	const btnRefs = useRef(new Map<string, HTMLButtonElement>());
	const [pill, setPill] = useState<PillRect | null>(null);
	const isTabs = as === 'tabs';
	const isChoice = as === 'choice';
	const roving = isTabs || isChoice;
	// `'choice'`: the one tab stop is the pressed option, else the last one
	// focused, else the first.
	const [focusValue, setFocusValue] = useState<T | null>(null);
	const tabStop: T | undefined =
		value ??
		(focusValue && options.some((o) => o.value === focusValue) ? focusValue : undefined) ??
		options[0]?.value;

	// Measure the active button's box relative to the container after layout,
	// and re-measure on resize. `useLayoutEffect` so the pill is positioned
	// before paint (no first-frame flash at 0,0). Both the container AND the
	// active button are observed: a segment can resize without the container
	// telling the whole story (e.g. a count in its label ticking over),
	// which would otherwise leave the pill on a stale rect.
	useLayoutEffect(() => {
		function measure() {
			const container = containerRef.current;
			const btn = value == null ? undefined : btnRefs.current.get(value);
			if (!container) return;
			if (!btn) {
				setPill(null);
				return;
			}
			setPill({ left: btn.offsetLeft, width: btn.offsetWidth });
		}
		measure();
		const ro = new ResizeObserver(measure);
		if (containerRef.current) ro.observe(containerRef.current);
		const activeBtn = value == null ? undefined : btnRefs.current.get(value);
		if (activeBtn) ro.observe(activeBtn);
		return () => ro.disconnect();
	}, [value, options]);

	// Roving Left/Right/Home/End navigation for tab semantics. Moving focus also
	// activates the tab (automatic activation), matching the WAI-ARIA tabs pattern
	// for a small, cheap-to-render lens switcher.
	function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>, current: T) {
		if (!roving) return;
		if (isChoice && (e.key === 'Delete' || e.key === 'Backspace')) {
			if (value != null && onClear) {
				e.preventDefault();
				onClear();
			}
			return;
		}
		// Tabs move from the selected tab; a choice moves from the focused option.
		const idx = options.findIndex((o) => o.value === (isChoice ? current : value));
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
		// A choice moves focus only (Space/Enter press); tabs activate on focus.
		if (isChoice) setFocusValue(next);
		else onChange(next);
		btnRefs.current.get(next)?.focus();
	}

	return (
		<div
			ref={containerRef}
			role={isTabs ? 'tablist' : ariaLabel || isChoice ? 'group' : undefined}
			aria-label={ariaLabel}
			aria-disabled={disabled || undefined}
			className={cn(
				// Structural backstop for the invariant above: even if a stale
				// rect ever slipped through, overflow past the control's border
				// never becomes visible or scrollable. The pill is inset
				// (its top/bottom offsets equal the padding), so nothing is cut at rest.
				'relative flex overflow-x-clip',
				styles.root,
				field && 'border-control-edge border',
				disabled && 'opacity-50',
				className,
			)}
			data-tone={tone}
		>
			{pill && (
				<motion.div
					aria-hidden="true"
					className={cn(
						'pointer-events-none absolute',
						styles.pill,
						activeOption?.activeTint?.pill,
					)}
					initial={false}
					animate={{ left: pill.left, width: pill.width }}
					// INVARIANT: the pill must never extend past the control's own
					// bounds. At rest the measurement guarantees it (offsetLeft/
					// offsetWidth are relative to the same padding box the pill
					// positions against); mid-animation it holds because this
					// spring is critically damped (damping ≥ 2·√stiffness ≈ 44.7):
					// `left` and `width` approach their targets monotonically, so
					// `left + width` stays inside the endpoints' envelope. The
					// underdamped original (damping 35) overshot proportionally to
					// the jump size, which inside an `overflow-x-auto` wrapper
					// (phone toolbars) could poke the pill's right edge past the
					// content width and blip a horizontal scrollbar. The root's
					// overflow-x-clip is the structural backstop for the same
					// invariant. Pinned by the bounds test in SegmentedToggle.test.
					transition={{ type: 'spring', stiffness: 500, damping: 45 }}
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
						aria-pressed={isTabs ? undefined : isActive}
						aria-controls={isTabs ? getControls?.(option.value) : undefined}
						tabIndex={
							isTabs
								? isActive
									? 0
									: -1
								: isChoice
									? option.value === tabStop
										? 0
										: -1
									: undefined
						}
						ref={(el) => {
							if (el) btnRefs.current.set(option.value, el);
							else btnRefs.current.delete(option.value);
						}}
						disabled={disabled}
						onClick={() => {
							if (isActive && onClear) onClear();
							else onChange(option.value);
						}}
						onFocus={isChoice ? () => setFocusValue(option.value) : undefined}
						onKeyDown={(e) => handleKeyDown(e, option.value)}
						className={cn(
							'relative flex items-center transition-colors',
							styles.segment,
							disabled ? 'cursor-not-allowed' : !isActive && 'cursor-pointer',
						)}
					>
						<span
							className={cn(
								'relative z-10 inline-flex items-center gap-1.5 whitespace-nowrap transition-colors',
								isActive
									? (option.activeTint?.text ?? styles.active)
									: disabled
										? styles.idle
										: cn(styles.idle, 'hover:text-foreground'),
							)}
						>
							{option.icon && (
								<span aria-hidden="true" className="inline-flex shrink-0">
									{option.icon}
								</span>
							)}
							{option.label}
						</span>
					</button>
				);
			})}
		</div>
	);
}
