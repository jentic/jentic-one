import { type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { ExpandableText } from '@/shared/ui/ExpandableText';
import { cn } from '@/shared/lib/utils';

interface PageHeaderProps {
	title: string;
	/** Short sentence beneath the title. */
	subtitle?: string;
	/** Optional icon/avatar rendered to the left of the title. */
	icon?: ReactNode;
	/** Right-aligned slot for buttons / controls. */
	actions?: ReactNode;
	/**
	 * When true (default) the title slides in with a spring entrance via
	 * framer-motion. Pass `animated={false}` in tests or wherever the
	 * motion would interfere.
	 */
	animated?: boolean;
	className?: string;
}

/**
 * Page header band: the route's title zone. A tonal band (surface-1 fading
 * into the page, a soft teal wash behind the title, a hairline foot) —
 * `.page-header-band` in `index.css` — so the title reads as its own zone above
 * the page's tonal surfaces. The foot is an accent divider: a 2px accent line
 * across the band, edge to edge, fading out toward the right end.
 * It escapes `PageShell`'s gutter and top padding with negative margins so the
 * band runs edge-to-edge from the top of the scroller.
 *
 * Always use this component at the top of every route inside
 * `<PageShell>`. Detail pages that need a "back to <parent>" affordance
 * should render a `<BackButton>` *underneath* the `<PageHeader>` rather
 * than baking it into the header.
 */
export function PageHeader({
	title,
	subtitle,
	icon,
	actions,
	animated = true,
	className,
}: PageHeaderProps) {
	const content = (
		<div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
			<div className="flex min-w-0 basis-full items-start gap-3 sm:flex-1 sm:basis-0">
				{icon && <div className="shrink-0">{icon}</div>}
				<div className="min-w-0 flex-1">
					{/* Integer line heights (25 / 32px) keep the band's foot — and the
					    accent on it — on whole pixels. */}
					<h1 className="font-heading text-foreground text-xl leading-tight font-semibold tracking-[-0.015em] md:text-[1.625rem] md:leading-8">
						{title}
					</h1>
					{subtitle && (
						<div className="mt-1">
							{/* A subtitle is free text: two lines, then on request
							    the rest (`ExpandableText`). */}
							<ExpandableText lines={2} className="text-foreground-sub text-sm">
								{subtitle}
							</ExpandableText>
						</div>
					)}
				</div>
			</div>
			{actions && (
				// `.page-header-actions` (index.css) lifts the header's buttons and
				// fields off the band with fill and a soft shadow (no edges), a
				// glow on the primary CTA and a filled field well. Keyed off
				// Button's `data-variant`, so pages just pick the right variant.
				<div className="page-header-actions flex shrink-0 items-center gap-2 self-center">
					{actions}
				</div>
			)}
		</div>
	);

	return (
		<div className={cn('page-header-band -mx-page-gutter -mt-6', className)}>
			<div className="px-4 pt-5 pb-4 md:pt-6 md:pb-5">
				{animated ? (
					<motion.div
						// Translate only: fading from opacity 0 renders the title
						// below contrast mid-entrance (same reason as `.animate-rise`).
						initial={{ y: -8 }}
						animate={{ y: 0 }}
						transition={{ duration: 0.25, ease: 'easeOut' }}
					>
						{content}
					</motion.div>
				) : (
					content
				)}
			</div>
		</div>
	);
}
