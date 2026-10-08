import React, { useCallback } from 'react';
import { Search, X } from 'lucide-react';
import { Input, inputEdgelessClasses } from '@/shared/ui/Input';
import { cn } from '@/shared/lib/utils';

type SearchInputSize = 'sm' | 'md';

/**
 * Where the field sits, which decides its surface (borderless unless `field`
 * — the lighter fill marks the field, the icon and placeholder identify it,
 * and focus draws an inset accent ring, stronger for keyboard focus):
 *   - `default` — one step lighter than whatever it sits on (`--field-bg`:
 *                 the page, a card, a sheet or a dialog)
 *   - `surface` — `surface-1` on the page background (a page toolbar, 36px)
 *   - `inset`   — one step lighter than a `surface-1` panel (a filter inside
 *                 a docked panel or a sheet, 34px)
 */
export type SearchInputTone = 'default' | 'surface' | 'inset';

const fieldText = 'placeholder:text-foreground-faint py-0';

const toneClasses: Record<SearchInputTone, Record<SearchInputSize, string>> = {
	default: {
		sm: cn(fieldText, 'h-8 text-[13px]'),
		md: cn(fieldText, 'h-9 text-[13.5px]'),
	},
	surface: {
		sm: cn(fieldText, 'bg-surface-1 h-8 text-[13px]'),
		md: cn(fieldText, 'bg-surface-1 h-9 text-[13.5px]'),
	},
	inset: {
		sm: cn(fieldText, 'bg-surface-field h-[34px] text-[13px]'),
		md: cn(fieldText, 'bg-surface-field h-[34px] text-[13px]'),
	},
};

type SearchInputProps = Omit<React.ComponentProps<'input'>, 'size' | 'type' | 'onChange'> & {
	value: string;
	onValueChange: (value: string) => void;
	onClear?: () => void;
	size?: SearchInputSize;
	loading?: boolean;
	icon?: React.ReactNode;
	/** Surface the field sits on (default: one step lighter than its container). */
	tone?: SearchInputTone;
	/**
	 * The field acts as a form field (part of a form the user fills in, not a
	 * toolbar/panel filter): keeps the inputs' faint resting edge. Default
	 * `false` — a search/filter is identified by its icon and placeholder.
	 */
	field?: boolean;
};

export const SearchInput = React.forwardRef<HTMLInputElement, SearchInputProps>(
	function SearchInput(
		{
			value,
			onValueChange,
			onClear,
			size = 'md',
			loading,
			icon,
			tone = 'default',
			field = false,
			className,
			...props
		},
		ref,
	) {
		const handleChange = useCallback(
			(e: React.ChangeEvent<HTMLInputElement>) => {
				onValueChange(e.target.value);
			},
			[onValueChange],
		);

		const handleClear = useCallback(() => {
			onValueChange('');
			onClear?.();
		}, [onValueChange, onClear]);

		const handleKeyDown = useCallback(
			(e: React.KeyboardEvent<HTMLInputElement>) => {
				if (e.key === 'Escape' && value) {
					e.preventDefault();
					e.stopPropagation();
					handleClear();
				}
			},
			[value, handleClear],
		);

		return (
			<div className={cn('relative', className)} data-tone={tone}>
				<Input
					ref={ref}
					type="search"
					value={value}
					onChange={handleChange}
					onKeyDown={handleKeyDown}
					size={size}
					startIcon={
						<span className="text-foreground-faint inline-flex">
							{icon ?? <Search className="h-3.5 w-3.5" />}
						</span>
					}
					className={cn(
						!field && inputEdgelessClasses,
						toneClasses[tone][size],
						value && 'pr-8',
						'[&::-webkit-search-cancel-button]:hidden [&::-webkit-search-decoration]:hidden',
					)}
					{...props}
				/>
				{value && !loading && (
					<button
						type="button"
						onClick={handleClear}
						className="text-foreground-faint hover:text-foreground absolute inset-y-0 right-2 flex items-center"
						aria-label="Clear search"
					>
						<X className="h-3.5 w-3.5" />
					</button>
				)}
				{loading && (
					<div className="text-muted-foreground absolute inset-y-0 right-2 flex items-center">
						<svg
							className="h-3.5 w-3.5 animate-spin"
							viewBox="0 0 24 24"
							fill="none"
							aria-hidden="true"
						>
							<circle
								className="opacity-25"
								cx="12"
								cy="12"
								r="10"
								stroke="currentColor"
								strokeWidth="4"
							/>
							<path
								className="opacity-75"
								fill="currentColor"
								d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
							/>
						</svg>
					</div>
				)}
			</div>
		);
	},
);

SearchInput.displayName = 'SearchInput';

export type { SearchInputProps };
