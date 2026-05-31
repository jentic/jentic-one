/* eslint-disable no-restricted-syntax */
import React, { useCallback } from 'react';
import { Search, X } from 'lucide-react';
import { Input } from './Input';
import { cn } from '@/lib/utils';

type SearchInputSize = 'sm' | 'md';

type SearchInputProps = Omit<React.ComponentProps<'input'>, 'size' | 'type' | 'onChange'> & {
	value: string;
	onValueChange: (value: string) => void;
	onClear?: () => void;
	size?: SearchInputSize;
	loading?: boolean;
	icon?: React.ReactNode;
};

export const SearchInput = React.forwardRef<HTMLInputElement, SearchInputProps>(
	({ value, onValueChange, onClear, size = 'md', loading, icon, className, ...props }, ref) => {
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
			<div className={cn('relative', className)}>
				<Input
					ref={ref}
					type="search"
					value={value}
					onChange={handleChange}
					onKeyDown={handleKeyDown}
					size={size}
					startIcon={icon ?? <Search className="h-3.5 w-3.5" />}
					className={cn(
						value && 'pr-8',
						'[&::-webkit-search-cancel-button]:hidden [&::-webkit-search-decoration]:hidden',
					)}
					{...props}
				/>
				{value && !loading && (
					<button
						type="button"
						onClick={handleClear}
						className="text-muted-foreground hover:text-foreground absolute inset-y-0 right-2 flex items-center"
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
