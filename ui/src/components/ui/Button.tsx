import React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'outline';
type Size = 'sm' | 'md' | 'lg' | 'icon';

const variantClasses: Record<Variant, string> = {
	primary: 'bg-primary text-background hover:bg-primary-hover disabled:opacity-50',
	secondary:
		'bg-muted border border-border text-foreground hover:bg-muted/60 disabled:opacity-50',
	danger: 'bg-danger/10 border border-danger/30 text-danger hover:bg-danger/20 disabled:opacity-50',
	ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-50',
	outline:
		'bg-primary/10 border border-primary/30 text-primary hover:bg-primary/20 disabled:opacity-50',
};

const sizeClasses: Record<Size, string> = {
	sm: 'px-3 py-1.5 text-sm gap-1.5',
	md: 'px-4 py-2 text-sm gap-2',
	lg: 'px-4 py-3 text-sm font-bold gap-2',
	icon: 'p-2',
};

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
	variant?: Variant;
	size?: Size;
	loading?: boolean;
	fullWidth?: boolean;
	children?: React.ReactNode;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
	(
		{
			variant = 'primary',
			size = 'md',
			loading = false,
			fullWidth = false,
			disabled,
			children,
			className,
			...props
		},
		ref,
	) => {
		return (
			<button
				ref={ref}
				type="button"
				disabled={disabled || loading}
				aria-busy={loading || undefined}
				aria-disabled={disabled || loading || undefined}
				className={cn(
					'focus-visible:ring-ring inline-flex cursor-pointer items-center justify-center rounded-lg font-medium transition-all focus-visible:ring-2 focus-visible:ring-offset-2',
					variantClasses[variant],
					sizeClasses[size],
					fullWidth && 'w-full',
					className,
				)}
				{...props}
			>
				{loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin" />}
				{children}
			</button>
		);
	},
);

Button.displayName = 'Button';
