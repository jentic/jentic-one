import type { FormHTMLAttributes, HTMLAttributes } from 'react';
import { cn } from '@/shared/lib/utils';

export interface AuthCardProps extends FormHTMLAttributes<HTMLFormElement> {
	/**
	 * `form` for a page whose card IS the form (sign-in, setup, password,
	 * invite); `div` for a status card (SSO callback, OAuth popup return).
	 * Form-only attributes are ignored when `as="div"`.
	 */
	as?: 'form' | 'div';
	/** Centre the card's text (status cards). */
	centered?: boolean;
}

/**
 * The standalone card of a pre-shell auth screen, centred on the page: a
 * `surface-1` tile with the hairline field edge and `shadow-elevated`, so it
 * lifts off the bare background on its own. Renders the full-height `<main>`
 * that centres it, so a page passes only its card content.
 */
export function AuthCard({
	as = 'form',
	centered = false,
	className,
	children,
	...props
}: AuthCardProps) {
	const cardClass = cn(
		'bg-surface-1 border-hairline-field shadow-elevated w-full max-w-sm rounded-lg border p-6 [--field-bg:var(--surface-field)]',
		centered && 'text-center',
		className,
	);
	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			{as === 'form' ? (
				<form className={cardClass} {...props}>
					{children}
				</form>
			) : (
				<div className={cardClass} {...(props as HTMLAttributes<HTMLDivElement>)}>
					{children}
				</div>
			)}
		</main>
	);
}
