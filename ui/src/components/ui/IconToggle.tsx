/* eslint-disable no-restricted-syntax */
import type { LucideIcon } from 'lucide-react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

/**
 * Single-segment descriptor for {@link IconToggle}. The `value` is what the
 * caller compares against; `icon` is rendered inside the segment; `label`
 * powers `aria-label` / `title` for screen readers and tooltips.
 */
export interface IconToggleOption {
	value: string;
	icon: LucideIcon;
	label?: string;
}

interface IconToggleProps {
	options: IconToggleOption[];
	value: string;
	onChange: (value: string) => void;
	className?: string;
	/**
	 * Group `aria-label` for the surrounding container — without this the
	 * toggle reads as a bare row of buttons to assistive tech.
	 */
	ariaLabel?: string;
	/**
	 * Prefix used for per-option `data-testid` attributes. Tests target
	 * individual segments via `${testIdPrefix}-${value}`. Defaults to
	 * `icon-toggle` — overridden by callers like the Discover density
	 * toggle that already have established testids (`density-grid`,
	 * `density-list`).
	 */
	testIdPrefix?: string;
	/**
	 * Optional `data-testid` for the outer container. Lets callers keep
	 * pre-existing testids without wrapping the component.
	 */
	containerTestId?: string;
}

const CSS_CLASSES = {
	CONTAINER: 'relative flex gap-0.5 rounded-lg border border-border bg-background p-0.5',
	OPTION_BUTTON:
		'relative z-0 flex h-9 w-9 items-center justify-center rounded-md transition-colors',
	OPTION_ACTIVE: 'bg-muted text-foreground',
	OPTION_INACTIVE: 'cursor-pointer text-muted-foreground hover:text-foreground hover:bg-muted/50',
	ICON: 'h-4 w-4',
} as const;

/**
 * Two-or-more icon-only segmented control with a `framer-motion` thumb that
 * slides between the active option. Ported from `@jentic-frontend-ui` so the
 * Discover density toggle (and any future binary icon toggles in jentic-mini)
 * share visual behaviour with the rest of the design system instead of
 * re-implementing chrome inline.
 */
export function IconToggle({
	options,
	value,
	onChange,
	className,
	ariaLabel,
	testIdPrefix = 'icon-toggle',
	containerTestId,
}: IconToggleProps) {
	const activeIndex = options.findIndex((option) => option.value === value);
	const optionWidth = 100 / options.length;
	const padding = '0.125rem';
	const paddingDouble = '0.25rem';

	return (
		<div
			className={cn(CSS_CLASSES.CONTAINER, className)}
			role="group"
			aria-label={ariaLabel}
			data-testid={containerTestId}
		>
			<motion.div
				className="bg-muted absolute rounded-md"
				style={{
					top: padding,
					bottom: padding,
				}}
				initial={false}
				animate={{
					left: `calc(${activeIndex * optionWidth}% + ${padding})`,
					width: `calc(${optionWidth}% - ${paddingDouble})`,
				}}
				transition={{
					type: 'spring',
					stiffness: 300,
					damping: 30,
				}}
				aria-hidden="true"
			/>

			{options.map((option) => {
				const isActive = option.value === value;
				const Icon = option.icon;
				const label = option.label ?? option.value;
				return (
					<button
						key={option.value}
						type="button"
						onClick={() => onChange(option.value)}
						className={cn(
							CSS_CLASSES.OPTION_BUTTON,
							isActive ? CSS_CLASSES.OPTION_ACTIVE : CSS_CLASSES.OPTION_INACTIVE,
						)}
						style={{ flex: '1 1 0', minWidth: 0 }}
						aria-label={label}
						aria-pressed={isActive}
						title={label}
						data-testid={`${testIdPrefix}-${option.value}`}
					>
						<Icon className={CSS_CLASSES.ICON} />
					</button>
				);
			})}
		</div>
	);
}
