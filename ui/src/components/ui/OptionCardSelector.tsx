import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Card-grid selector — single-select, radio-group semantics.
 *
 * Faithful port of the webapp's "Choose Authentication Method"
 * card pattern (`AuthTypeCard` in `jentic-webapp`). Each option
 * renders as a bordered card with a leading icon, a bold name,
 * and (in the default variant) a one-line description plus a
 * right-aligned circular check indicator that fills with the
 * primary color when selected.
 *
 * Reach for this when you have 2–6 mutually-exclusive choices
 * that benefit from being *seen* rather than read — e.g. picking
 * an authentication method, an import source, a workflow type.
 * For dense or 2-way pivots (filters, view toggles), use
 * `<SegmentedToggle>` instead.
 *
 * Two variants:
 *
 *  - **`default`** — generous `border-2 rounded-xl p-4` cards
 *    with full description + check indicator. Use for "weighty"
 *    choices the user should consider (auth method, the API vs
 *    Workflow kind pivot at the top of the import dialog).
 *
 *  - **`compact`** — quieter `border rounded-lg p-3` cards with
 *    no description and no check indicator. The selected state
 *    is conveyed by the primary-tinted border + softer background
 *    fill. Use for three-up rows where each option is a quick
 *    tap and the description would be redundant with adjacent UI
 *    (e.g. URL / Paste / Upload, where the input below makes the
 *    choice obvious).
 *
 * Behaviour:
 *
 *  - Each card is a `<button role="radio">`. The wrapper has
 *    `role="radiogroup"` and an aria-label / aria-labelledby.
 *  - `whileHover` / `whileTap` scale animations match the webapp's
 *    AuthTypeCard so the design language stays aligned.
 *  - The grid template is configurable via `columns` — 1 column
 *    for stacked layouts, 2 for the default "auth method"-style
 *    side-by-side, 3 for compact triples.
 *
 * NOTE: This is intentionally *not* themed by `tone`. Selection
 * always uses the primary color so the visual hierarchy stays
 * predictable across surfaces.
 */

export type OptionCardVariant = 'default' | 'compact';

export interface OptionCardItem<T extends string = string> {
	value: T;
	label: string;
	description?: string;
	icon?: ReactNode;
	/** Renders a small "Recommended" / "Beta" pill in the top-right corner. */
	badge?: string;
	/** Renders the item but disables interaction. */
	disabled?: boolean;
}

export interface OptionCardSelectorProps<T extends string = string> {
	options: OptionCardItem<T>[];
	value: T;
	onChange: (value: T) => void;
	/** Number of columns at the `sm:` breakpoint. */
	columns?: 1 | 2 | 3;
	variant?: OptionCardVariant;
	ariaLabel?: string;
	ariaLabelledBy?: string;
	className?: string;
	'data-testid'?: string;
}

const COLUMN_CLASSES: Record<NonNullable<OptionCardSelectorProps['columns']>, string> = {
	1: 'grid-cols-1',
	2: 'grid-cols-1 sm:grid-cols-2',
	3: 'grid-cols-1 sm:grid-cols-3',
};

export function OptionCardSelector<T extends string = string>({
	options,
	value,
	onChange,
	columns = 2,
	variant = 'default',
	ariaLabel,
	ariaLabelledBy,
	className,
	'data-testid': dataTestId,
}: OptionCardSelectorProps<T>) {
	const isCompact = variant === 'compact';

	return (
		<div
			role="radiogroup"
			aria-label={ariaLabel}
			aria-labelledby={ariaLabelledBy}
			className={cn('grid gap-2', !isCompact && 'gap-3', COLUMN_CLASSES[columns], className)}
			data-testid={dataTestId}
		>
			{options.map((option) => {
				const isSelected = value === option.value;
				const isDisabled = Boolean(option.disabled);
				// A selected card is "inert" — clicking it again is a
				// no-op, so the affordance should reflect that. No
				// pointer cursor, no hover scale, no press scale, no
				// hover bg/border swap. Lets users see at a glance
				// which card is the current state vs. the others
				// they can switch to.
				const isInert = isSelected || isDisabled;

				const baseClass = cn(
					'relative w-full text-left transition-colors',
					isCompact ? 'rounded-lg border p-3' : 'rounded-xl border-2 p-4',
					isInert ? 'cursor-default' : 'cursor-pointer',
				);
				const stateClass = isSelected
					? isCompact
						? 'border-primary/60 bg-primary/[0.07]'
						: 'border-primary bg-primary/5 shadow-sm'
					: cn(
							'bg-background hover:bg-muted/30',
							isCompact
								? 'border-border/70 hover:border-border'
								: 'border-border hover:border-primary/50',
						);
				const disabledClass =
					'hover:border-border hover:bg-background cursor-not-allowed opacity-50';

				return (
					<motion.button
						key={option.value}
						type="button"
						role="radio"
						aria-checked={isSelected}
						aria-disabled={isDisabled || undefined}
						disabled={isDisabled}
						onClick={() => {
							if (!isDisabled && !isSelected) onChange(option.value);
						}}
						whileHover={isInert ? undefined : { scale: 1.01 }}
						whileTap={isInert ? undefined : { scale: 0.99 }}
						className={cn(baseClass, stateClass, isDisabled && disabledClass)}
						data-value={option.value}
						data-selected={isSelected || undefined}
					>
						{option.badge ? (
							<span className="bg-primary text-primary-foreground absolute -top-2 right-3 rounded-full px-2 py-0.5 text-[10px] font-medium">
								{option.badge}
							</span>
						) : null}

						<div className={cn('flex items-start', isCompact ? 'gap-2.5' : 'gap-3')}>
							{option.icon ? (
								<span
									className={cn(
										'shrink-0 transition-colors',
										!isCompact && 'mt-0.5',
										isSelected ? 'text-primary' : 'text-muted-foreground',
									)}
									aria-hidden="true"
								>
									{option.icon}
								</span>
							) : null}

							<div className="min-w-0 flex-1">
								<div className="flex items-center gap-2">
									<span
										className={cn(
											'text-foreground font-semibold',
											isCompact ? 'text-[13px]' : 'text-sm',
										)}
									>
										{option.label}
									</span>
								</div>
								{option.description && !isCompact ? (
									<p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs leading-snug">
										{option.description}
									</p>
								) : null}
							</div>

							{!isCompact ? (
								<span
									className={cn(
										'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
										isSelected
											? 'border-primary bg-primary'
											: 'border-muted-foreground/30',
									)}
									aria-hidden="true"
								>
									{isSelected ? (
										<Check
											className="text-primary-foreground"
											size={12}
											strokeWidth={3}
										/>
									) : null}
								</span>
							) : null}
						</div>
					</motion.button>
				);
			})}
		</div>
	);
}
