import React, { useId } from 'react';
import { cn } from '@/shared/lib/utils';
import { inputSurfaceClasses } from '@/shared/ui/Input';

type SelectProps = React.ComponentProps<'select'> & {
	error?: string;
};

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
	{ error, className, children, id, ...props },
	ref,
) {
	const generatedId = useId();
	const selectId = id ?? generatedId;
	const errorId = error ? `${selectId}-error` : undefined;

	return (
		<div className="w-full">
			<select
				ref={ref}
				id={selectId}
				aria-describedby={errorId}
				aria-invalid={error ? true : undefined}
				className={cn(
					'bg-field w-full px-3 py-2 text-sm',
					inputSurfaceClasses,
					error && 'border-danger focus:border-danger',
					className,
				)}
				{...props}
			>
				{children}
			</select>
			{error && (
				<p id={errorId} className="text-danger mt-1 text-xs" role="alert">
					{error}
				</p>
			)}
		</div>
	);
});

Select.displayName = 'Select';

export type { SelectProps };
