import React, { useState, useId } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

type InputSize = 'sm' | 'md';

const sizeClasses: Record<InputSize, string> = {
	sm: 'px-3 py-1.5 text-sm',
	md: 'px-4 py-2 text-sm',
};

type InputProps = Omit<React.ComponentProps<'input'>, 'size'> & {
	error?: string;
	size?: InputSize;
	showPasswordToggle?: boolean;
	startIcon?: React.ReactNode;
};

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
	{ error, size = 'md', showPasswordToggle, startIcon, className, type, id, ...props },
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
						'bg-card border-border text-foreground placeholder:text-muted-foreground w-full rounded-lg border transition-colors',
						'focus:border-primary focus:outline-hidden',
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
