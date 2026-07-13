/**
 * OptionCardSelector — single-select, radio-group card grid.
 *
 * Faithful port of jentic-mini's workspace import picker so the Import dialog
 * reads the same here as it does there: each option is a bordered card with a
 * leading icon, a bold label, an optional one-line description (default
 * variant), and a right-aligned circular check that fills with the primary
 * colour when selected.
 *
 * Two variants:
 *  - `default` — generous `border-2 rounded-xl p-4` cards with description +
 *    check indicator. Use for weighty choices the user should consider.
 *  - `compact` — quieter `border rounded-lg p-3` cards, no description, no
 *    check. Selection is conveyed by the primary-tinted border + soft fill.
 *    Use for three-up rows (e.g. URL / Paste / Upload).
 *
 * Kept module-local (not promoted to `@/shared/ui`) so the Workspace PR doesn't
 * churn the shared barrel; if another module wants it, lift it then.
 */
import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Check } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

export type OptionCardVariant = 'default' | 'compact';

export interface OptionCardItem<T extends string = string> {
	value: T;
	label: string;
	description?: string;
	icon?: ReactNode;
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
				// A selected card is "inert" — clicking it again is a no-op, so the
				// affordance reflects that (no pointer cursor, no hover/press scale).
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
