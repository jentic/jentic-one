import type { AnchorHTMLAttributes } from 'react';
import { Link, type LinkProps } from 'react-router-dom';
import { cn } from '@/shared/lib/utils';
import {
	buttonBase,
	buttonVariantClasses,
	buttonSizeClasses,
	type ButtonVariant,
	type ButtonSize,
} from '@/shared/ui/Button';

type AppLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> &
	Omit<LinkProps, 'to'> & {
		href: string;
		external?: boolean;
		/**
		 * Render the link with `Button`'s look (a link that acts like a button —
		 * e.g. an "Import API" CTA). Reuses `Button`'s exported variant/size
		 * tokens so the two never drift. `AppLink`'s own `FOCUS_RING` supplies the
		 * focus affordance, so the button focus ring is intentionally not applied.
		 */
		variant?: ButtonVariant;
		size?: ButtonSize;
	};

const EXTERNAL_RE = /^([a-z][a-z0-9+.-]*:|\/\/)/i;
const UNSAFE_RE = /^(javascript|data|vbscript):/i;

/**
 * Default keyboard focus affordance. Tailwind's preflight resets the UA outline
 * on `<a>`, so without this internal/external links have no visible focus ring
 * (WCAG 2.4.7). Applied to the navigable variants; merged with any `className`.
 */
const FOCUS_RING =
	'rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';

function isExternalHref(href: string): boolean {
	return EXTERNAL_RE.test(href);
}

/**
 * Router-aware link that hardens against XSS. Hrefs using dangerous
 * schemes (`javascript:`, `data:`, `vbscript:`) are refused and rendered
 * as an inert `<span role="link" aria-disabled>` instead of a navigable
 * anchor. External hrefs open in a new tab with `noopener noreferrer`;
 * everything else goes through react-router's `<Link>`. Navigable links
 * carry a visible `focus-visible` ring for keyboard users.
 */
export function AppLink({
	href,
	external,
	target,
	rel,
	children,
	className,
	variant,
	size,
	...props
}: AppLinkProps) {
	// When a button look is requested, compose Button's shared tokens (base +
	// variant + size). Skip Button's focus ring — FOCUS_RING below owns it.
	const buttonLook =
		variant != null || size != null
			? cn(
					buttonBase,
					variant != null && buttonVariantClasses[variant],
					size != null && buttonSizeClasses[size],
				)
			: undefined;
	const mergedClassName = cn(buttonLook, className);

	if (UNSAFE_RE.test(href)) {
		return (
			<span {...props} className={mergedClassName} role="link" aria-disabled="true">
				{children}
			</span>
		);
	}

	if (external || isExternalHref(href)) {
		return (
			<a
				href={href}
				target={target ?? '_blank'}
				rel={rel ?? 'noopener noreferrer'}
				className={cn(FOCUS_RING, mergedClassName)}
				{...props}
			>
				{children}
			</a>
		);
	}

	return (
		<Link to={href} className={cn(FOCUS_RING, mergedClassName)} {...props}>
			{children}
		</Link>
	);
}

export type { AppLinkProps };
