/**
 * ApiViewToggle — the compact list⇄cards lens switcher on the "Can call"
 * toolbar. A two-option radiogroup of icon buttons: `List` shows the
 * hover-reveal row tree; `Cards` shows the dense identity grid.
 *
 * It is a `role="radiogroup"` of `role="radio"` buttons (not the shared
 * `SegmentedToggle`, whose animated text pill and `aria-pressed`/tab semantics
 * don't fit two icon-only choices). Roving tabIndex + arrow/Home/End keys move
 * AND select, matching the WAI-ARIA radiogroup pattern; Lucide `List` /
 * `LayoutGrid` glyphs are decorative, and each choice is named by its
 * `aria-label`. No hover tooltip — the two glyphs speak for themselves.
 */
import { useRef, type KeyboardEvent } from 'react';
import { LayoutGrid, List } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { ApiView } from '@/modules/agents/lib/apiView';

const OPTIONS: { value: ApiView; label: string; Icon: typeof List }[] = [
	{ value: 'list', label: 'List view', Icon: List },
	{ value: 'cards', label: 'Cards view', Icon: LayoutGrid },
];

interface ApiViewToggleProps {
	value: ApiView;
	onChange: (view: ApiView) => void;
	/** aria-label for the group (names what the lens switches). */
	ariaLabel?: string;
	className?: string;
}

export function ApiViewToggle({
	value,
	onChange,
	ariaLabel = 'API layout',
	className,
}: ApiViewToggleProps) {
	const btnRefs = useRef(new Map<ApiView, HTMLButtonElement>());

	// Arrow/Home/End move focus AND the selection, per the radiogroup pattern.
	function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
		const idx = OPTIONS.findIndex((o) => o.value === value);
		if (idx === -1) return;
		let nextIdx: number | null = null;
		if (event.key === 'ArrowRight' || event.key === 'ArrowDown')
			nextIdx = (idx + 1) % OPTIONS.length;
		else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp')
			nextIdx = (idx - 1 + OPTIONS.length) % OPTIONS.length;
		else if (event.key === 'Home') nextIdx = 0;
		else if (event.key === 'End') nextIdx = OPTIONS.length - 1;
		if (nextIdx == null) return;
		event.preventDefault();
		const next = OPTIONS[nextIdx].value;
		onChange(next);
		btnRefs.current.get(next)?.focus();
	}

	return (
		<div
			role="radiogroup"
			aria-label={ariaLabel}
			data-testid="api-view-toggle"
			className={cn(
				'bg-surface-field inline-flex shrink-0 gap-0.5 rounded-[9px] p-0.5',
				className,
			)}
		>
			{OPTIONS.map(({ value: optionValue, label, Icon }) => {
				const checked = value === optionValue;
				return (
					<button
						key={optionValue}
						type="button"
						role="radio"
						aria-checked={checked}
						aria-label={label}
						tabIndex={checked ? 0 : -1}
						data-view={optionValue}
						ref={(el) => {
							if (el) btnRefs.current.set(optionValue, el);
							else btnRefs.current.delete(optionValue);
						}}
						onClick={() => onChange(optionValue)}
						onKeyDown={onKeyDown}
						className={cn(
							'grid h-6 w-7 cursor-pointer place-items-center rounded-[7px] outline-none',
							'transition-[color,background-color,box-shadow] duration-150 motion-reduce:transition-none',
							'focus-visible:shadow-[0_0_0_1.5px_hsl(var(--primary)/0.7)]',
							checked
								? 'bg-surface-tonal-hover text-white shadow-[0_1px_2px_hsl(192_35%_4%/0.5)]'
								: 'text-foreground-faint hover:text-foreground-lighter',
						)}
					>
						<Icon aria-hidden="true" className="h-[15px] w-[15px]" />
					</button>
				);
			})}
		</div>
	);
}
