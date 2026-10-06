import type { AnchorHTMLAttributes } from 'react';
import { Link, type LinkProps } from 'react-router';
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

/** The only schemes a link may navigate to outside the app. */
const EXTERNAL_RE = /^(https?|mailto):/i;
/** An in-app path: one `/`, not followed by another (`//host` is protocol-relative). */
const INTERNAL_RE = /^\/(?!\/)/;

type HrefKind = 'internal' | 'external' | 'inert';

/**
 * Classify `href` the way a browser's URL parser will read it: C0 controls
 * and spaces are stripped (a superset of what the parser drops — so
 * ` javascript:` or `java\tscript:` can't hide a scheme) and `\` counts as
 * `/` (so `/\evil.com` and `\\evil.com` read as the protocol-relative
 * `//evil.com` they resolve to). It's an allowlist: `http:`, `https:` and
 * `mailto:` are external, a single-`/` path is internal, and anything else —
 * `javascript:`, `data:`, `blob:`, `file:`, `//host`, a bare relative path —
 * is inert.
 */
function classifyHref(href: string): HrefKind {
	// eslint-disable-next-line no-control-regex -- matching control characters is the point
	const normalised = href.replace(/[\u0000-\u0020]/g, '').replace(/\\/g, '/');
	if (EXTERNAL_RE.test(normalised)) return 'external';
	if (INTERNAL_RE.test(normalised)) return 'internal';
	return 'inert';
}

/**
 * Default keyboard focus affordance. Tailwind's preflight resets the UA outline
 * on `<a>`, so without this internal/external links have no visible focus ring
 * (WCAG 2.4.7). Applied to the navigable variants; merged with any `className`.
 */
const FOCUS_RING =
	'rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';

/**
 * Router-aware link that hardens against XSS and open redirects. Only
 * `http:`/`https:`/`mailto:` hrefs and single-`/` in-app paths navigate (see
 * `classifyHref`); anything else renders as an inert
 * `<span role="link" aria-disabled>` instead of an anchor. External hrefs
 * (or `external`) open in a new tab with `noopener noreferrer`; in-app paths
 * go through react-router's `<Link>`. Navigable links carry a visible
 * `focus-visible` ring for keyboard users.
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

	const kind = classifyHref(href);
	if (kind === 'inert') {
		return (
			<span {...props} className={mergedClassName} role="link" aria-disabled="true">
				{children}
			</span>
		);
	}

	if (external || kind === 'external') {
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
