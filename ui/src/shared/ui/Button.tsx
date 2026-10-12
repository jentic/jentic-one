import React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'outline' | 'tonal';
type Size = 'sm' | 'md' | 'lg' | 'icon' | 'xs' | 'icon-xs';

/**
 * Shared base classes for the button *look* (layout, radius, font, focus ring,
 * transitions). Exported so navigable primitives (e.g. `AppLink`) can render a
 * link that looks like a button without re-implementing — or drifting from —
 * these tokens. `AppLink` supplies its own focus ring, so the button base is
 * split from the ring below.
 */
const buttonBase =
	'inline-flex cursor-pointer items-center justify-center rounded-field font-semibold transition-[transform,background-color,border-color,color,box-shadow] duration-[140ms] ease-out active:scale-[0.98] disabled:cursor-not-allowed motion-reduce:active:scale-100';

const buttonFocusRing =
	'focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none';

/**
 * Variants. A surface is raised by lightening it, never by a border:
 *   - `primary`   — the filled accent CTA; one per surface (sheet/dialog footer, page action)
 *   - `tonal`     — in-context verbs on rows and cards (a lighter step of the surface)
 *   - `secondary` — a quiet standalone action (tonal fill, full-strength text)
 *   (No button carries an edge — not on cards, docks, ledgers or the rules
 *   editor: the fill says "button". Edges are for things you type into or
 *   toggle — Input/Select/Textarea, field SegmentedToggles, method chips.)
 *   - `outline`   — an accent-tinted action without the fill weight of `primary`
 *   - `ghost`     — icon/quiet actions (close, copy, toolbar glyphs)
 *   - `danger`    — destructive: an 18% red fill + red text (≈1.45:1 fill, ≈6:1 text)
 */
const variantClasses: Record<Variant, string> = {
	// Disabled CTA: a quiet raised fill with legible `sub` text (≥5:1) rather
	// than a 50% fade, so it still reads as the (unavailable) action.
	primary:
		'bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-surface-quiet-cta disabled:text-foreground-sub',
	secondary: 'bg-surface-tonal text-foreground hover:bg-surface-tonal-hover disabled:opacity-50',
	danger: 'bg-danger/18 text-danger hover:bg-danger/26 disabled:opacity-50',
	ghost: 'text-muted-foreground hover:text-foreground hover:bg-tint-2 disabled:opacity-50',
	outline: 'bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-50',
	tonal: 'bg-surface-tonal text-foreground-sub font-bold hover:bg-surface-tonal-hover hover:text-foreground disabled:opacity-50',
};

const sizeClasses: Record<Size, string> = {
	// 32px — header actions, toolbars, most inline buttons.
	sm: 'px-3 py-1.5 text-[13px] leading-5 gap-1.5',
	// 36px — the sheet/dialog CTA.
	md: 'px-4 py-2 text-[13.5px] leading-5 gap-2',
	lg: 'px-4 py-3 text-sm font-bold gap-2',
	icon: 'p-2 rounded-md',
	// 28px compact row/card actions.
	xs: 'h-7 px-[11px] text-xs gap-1.5 rounded-md',
	'icon-xs': 'h-7 w-7 p-0 rounded-md',
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
			data-variant={variant}
			data-size={size}
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
