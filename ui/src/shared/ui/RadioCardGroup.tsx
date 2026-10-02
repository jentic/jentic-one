import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

/**
 * RadioCardGroup — a single-select list of bordered, always-visible option
 * rows ("radio cards"). Visually it matches the divided, rounded list the
 * scope checkboxes use (`divide-y` inside a bordered `rounded-lg`), with a
 * round primary indicator + tinted row for the selected option.
 *
 * Implements the WAI-ARIA radio-group pattern:
 *  - `role="radiogroup"` on the list, named via `ariaLabelledBy`/`ariaLabel`;
 *  - each row is `role="radio"` with `aria-checked`, labelled by its `label`
 *    and described by its `description`;
 *  - roving tabindex — the group is ONE tab stop (the checked option, else
 *    the first enabled one);
 *  - Arrow Up/Down/Left/Right move focus AND selection (skipping disabled
 *    options, wrapping at the ends), Home/End jump; Enter/Space select the
 *    focused option (native button activation).
 *
 * `maxHeightClass` caps the list with internal scrolling so a long option
 * list never pushes the content below it out of reach.
 */

export interface RadioCardOption<T extends string = string> {
	value: T;
	/** Accessible name + visible title of the option. */
	label: string;
	/** Secondary line under the label (also the option's accessible description). */
	description?: ReactNode;
	/** Leading visual (avatar, icon chip…). Decorative wrappers are the caller's call. */
	leading?: ReactNode;
	/** Trailing content before the indicator (e.g. a status badge). */
	trailing?: ReactNode;
	disabled?: boolean;
	/** Render the row in a quieter, muted style (e.g. a "none" option). */
	muted?: boolean;
}

export interface RadioCardGroupProps<T extends string = string> {
	options: RadioCardOption<T>[];
	/** Currently-selected value; `null` means nothing is selected. */
	value: T | null;
	onChange: (value: T) => void;
	ariaLabel?: string;
	ariaLabelledBy?: string;
	/** Disable the whole group (locked selection). */
	disabled?: boolean;
	/** Tailwind max-height class for the scroll container (e.g. `max-h-64`). */
	maxHeightClass?: string;
	className?: string;
	'data-testid'?: string;
}

export function RadioCardGroup<T extends string = string>({
	options,
	value,
	onChange,
	ariaLabel,
	ariaLabelledBy,
	disabled = false,
	maxHeightClass,
	className,
	'data-testid': dataTestId,
}: RadioCardGroupProps<T>) {
	const baseId = useId();
	const refs = useRef(new Map<string, HTMLButtonElement>());

	const isEnabled = (o: RadioCardOption<T>): boolean => !disabled && !o.disabled;
	const checkedIdx = options.findIndex((o) => o.value === value && isEnabled(o));
	const tabStopIdx = checkedIdx !== -1 ? checkedIdx : options.findIndex(isEnabled);

	function select(idx: number): void {
		const opt = options[idx];
		if (!opt || !isEnabled(opt)) return;
		if (opt.value !== value) onChange(opt.value);
		refs.current.get(opt.value)?.focus();
	}

	/** Next enabled index from `from` stepping by `delta` (wrapping). */
	function step(from: number, delta: 1 | -1): number {
		const n = options.length;
		for (let i = 1; i <= n; i++) {
			const idx = (from + delta * i + n * i) % n;
			if (isEnabled(options[idx])) return idx;
		}
		return from;
	}

	function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>, idx: number): void {
		let next: number | null = null;
		if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = step(idx, 1);
		else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') next = step(idx, -1);
		// Home: first enabled (step forward from the last slot, wrapping to 0).
		else if (e.key === 'Home') next = step(options.length - 1, 1);
		// End: last enabled (step back from 0, wrapping to n-1).
		else if (e.key === 'End') next = step(0, -1);
		if (next == null) return;
		e.preventDefault();
		select(next);
	}

	return (
		<div
			role="radiogroup"
			aria-label={ariaLabel}
			aria-labelledby={ariaLabelledBy}
			aria-disabled={disabled || undefined}
			data-testid={dataTestId}
			className={cn(
				'border-border divide-border divide-y overflow-hidden rounded-lg border',
				maxHeightClass && cn(maxHeightClass, 'overflow-y-auto'),
				className,
			)}
		>
			{options.map((option, idx) => {
				const checked = option.value === value;
				const enabled = isEnabled(option);
				const labelId = `${baseId}-${idx}-label`;
				const descId = option.description ? `${baseId}-${idx}-desc` : undefined;
				return (
					<button
						key={option.value}
						ref={(el) => {
							if (el) refs.current.set(option.value, el);
							else refs.current.delete(option.value);
						}}
						type="button"
						role="radio"
						aria-checked={checked}
						aria-labelledby={labelId}
						aria-describedby={descId}
						disabled={!enabled}
						tabIndex={idx === tabStopIdx ? 0 : -1}
						onClick={() => select(idx)}
						onKeyDown={(e) => handleKeyDown(e, idx)}
						data-value={option.value}
						className={cn(
							'flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors',
							'focus-visible:ring-primary/60 focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset',
							checked ? 'bg-primary/[0.07]' : enabled && 'hover:bg-muted/40',
							enabled ? 'cursor-pointer' : 'cursor-not-allowed',
							!enabled && !checked && 'opacity-50',
						)}
					>
						{option.leading ? <span className="shrink-0">{option.leading}</span> : null}
						<span className="min-w-0 flex-1">
							<span
								id={labelId}
								className={cn(
									'block truncate text-sm',
									option.muted
										? 'text-muted-foreground'
										: 'text-foreground font-medium',
									checked && 'text-foreground',
								)}
							>
								{option.label}
							</span>
							{option.description ? (
								<span
									id={descId}
									className="text-muted-foreground mt-0.5 block text-xs leading-snug"
								>
									{option.description}
								</span>
							) : null}
						</span>
						{option.trailing ? (
							<span className="shrink-0">{option.trailing}</span>
						) : null}
						<span
							aria-hidden="true"
							className={cn(
								'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors',
								checked ? 'border-primary bg-primary' : 'border-border border-2',
							)}
						>
							{checked ? (
								<Check
									className="text-primary-foreground h-3 w-3"
									strokeWidth={3}
								/>
							) : null}
						</span>
					</button>
				);
			})}
		</div>
	);
}
