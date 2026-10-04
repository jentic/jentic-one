import React, { useState, useId } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

type InputSize = 'sm' | 'md';

const sizeClasses: Record<InputSize, string> = {
	sm: 'px-3 py-1.5 text-sm',
	md: 'px-3.5 py-2 text-sm',
};

/**
 * The field's fill. `auto` (default) is one step lighter than whatever it sits
 * on — the page, a card, a sheet or a dialog set `--field-bg`. `inset` forces
 * the lighter step (a field inside a `surface-1` panel that doesn't set it).
 */
export type InputTone = 'auto' | 'inset';

/**
 * Tonal form field with the shared resting edge (`--control-edge`, the same
 * colour as `.edged-controls`). The fill marks it, the edge keeps an empty
 * field easy to spot on any surface, focus swaps the edge
 * for an inset accent ring (stronger for keyboard focus), and an error turns
 * the edge red.
 */
export const inputSurfaceClasses =
	'text-foreground placeholder:text-input-placeholder rounded-field border border-control-edge enabled:hover:border-control-edge-hover transition-[background-color,box-shadow,border-color] duration-[140ms] focus:border-transparent focus:shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.35)] focus:outline-hidden focus-visible:shadow-[inset_0_0_0_1.5px_hsl(var(--primary)/0.6)] disabled:cursor-not-allowed disabled:opacity-60';

/**
 * Drops the resting edge — for search/filter fields in toolbars and panels,
 * where the icon and placeholder identify the field.
 */
export const inputEdgelessClasses = 'border-transparent';

const toneClasses: Record<InputTone, string> = {
	auto: 'bg-field',
	inset: 'bg-surface-field',
};

type InputProps = Omit<React.ComponentProps<'input'>, 'size'> & {
	error?: string;
	size?: InputSize;
	tone?: InputTone;
	showPasswordToggle?: boolean;
	startIcon?: React.ReactNode;
};

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
	{
		error,
		size = 'md',
		tone = 'auto',
		showPasswordToggle,
		startIcon,
		className,
		type,
		id,
		...props
	},
	ref,
) {
	const [showPassword, setShowPassword] = useState(false);
	const generatedId = useId();
	const inputId = id ?? generatedId;
	const errorId = error ? `${inputId}-error` : undefined;

	const isPassword = type === 'password';
	const effectiveType = isPassword && showPassword ? 'text' : type;

	return (
		<div className="w-full">
			<div className="relative">
				{startIcon && (
					<div className="text-muted-foreground pointer-events-none absolute inset-y-0 left-3 flex items-center">
						{startIcon}
					</div>
				)}
				<input
					ref={ref}
					id={inputId}
					type={effectiveType}
					aria-describedby={errorId}
					aria-invalid={error ? true : undefined}
					className={cn(
						'w-full',
						inputSurfaceClasses,
						toneClasses[tone],
						sizeClasses[size],
						startIcon && 'pl-9',
						isPassword && showPasswordToggle && 'pr-10',
						error && 'border-danger focus:border-danger',
						className,
					)}
					{...props}
				/>
				{isPassword && showPasswordToggle && (
					<button
						type="button"
						tabIndex={-1}
						onClick={() => setShowPassword(!showPassword)}
						className="text-muted-foreground hover:text-foreground absolute top-1/2 right-3 -translate-y-1/2"
						aria-label={showPassword ? 'Hide password' : 'Show password'}
					>
						{showPassword ? (
							<EyeOff className="h-4 w-4" />
						) : (
							<Eye className="h-4 w-4" />
						)}
					</button>
				)}
			</div>
			{error && (
				<p id={errorId} className="text-danger mt-1 text-xs" role="alert">
					{error}
				</p>
			)}
		</div>
	);
});

Input.displayName = 'Input';

export type { InputProps };
