import React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'outline';
type Size = 'sm' | 'md' | 'lg' | 'icon';

/**
 * Shared base classes for the button *look* (layout, radius, font, focus ring,
 * transitions). Exported so navigable primitives (e.g. `AppLink`) can render a
 * link that looks like a button without re-implementing — or drifting from —
 * these tokens. `AppLink` supplies its own focus ring, so the button base is
 * split from the ring below.
 */
const buttonBase =
	'inline-flex cursor-pointer items-center justify-center rounded-lg font-medium transition-[transform,background-color,border-color,color,box-shadow] duration-150 ease-out active:scale-[0.98] disabled:cursor-not-allowed motion-reduce:active:scale-100';

const buttonFocusRing =
	'focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none';

const variantClasses: Record<Variant, string> = {
	primary: 'bg-primary text-background shadow-card hover:bg-primary-hover disabled:opacity-50',
	secondary:
		'bg-muted border border-border text-foreground hover:bg-muted/60 hover:border-border-hover disabled:opacity-50',
	danger: 'bg-danger/10 border border-danger/30 text-danger hover:bg-danger/20 disabled:opacity-50',
	ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-50',
	outline:
		'bg-primary/10 border border-primary/30 text-primary hover:bg-primary/20 hover:border-primary/50 disabled:opacity-50',
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

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
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
) {
	return (
		<button
			ref={ref}
			type="button"
			disabled={disabled || loading}
			aria-busy={loading || undefined}
			aria-disabled={disabled || loading || undefined}
			className={cn(
				buttonBase,
				buttonFocusRing,
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
});

Button.displayName = 'Button';

export { buttonBase, variantClasses as buttonVariantClasses, sizeClasses as buttonSizeClasses };
export type { ButtonProps, Variant as ButtonVariant, Size as ButtonSize };
