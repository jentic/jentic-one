import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
	title: string;
	/** Short sentence beneath the title. */
	subtitle?: string;
	/** Right-aligned slot for buttons / controls. */
	actions?: ReactNode;
	/**
	 * When true (default) the title slides in with a spring entrance via
	 * framer-motion. Pass `animated={false}` in tests or wherever the motion
	 * would interfere.
	 */
	animated?: boolean;
	className?: string;
}

/**
 * Full-bleed page header band that mirrors the design language of
 * `jentic-webapp`'s `<PageHeader>`.
 *
 * It escapes the `p-4 md:p-6` padding that `Layout` applies by using negative
 * margins, so the gradient band stretches edge-to-edge without requiring a
 * `PageShell` or `Layout` change.
 *
 * Always use this component at the top of every route inside `<PageShell>`.
 * Do NOT reach for a raw `<h1>` on a new page.
 *
 * Detail pages that need a "back to <parent>" affordance should NOT bake
 * it into the header — the header is intentionally invariant across all
 * pages so users land in the same visual chrome no matter where they are.
 * Render a `<BackButton>` *underneath* the `<PageHeader>` instead, the
 * way `ToolkitDetailPage` and `WorkflowDetailPage` do.
 */
export function PageHeader({
	title,
	subtitle,
	actions,
	animated = true,
	className,
}: PageHeaderProps) {
	const content = (
		<div className="flex items-start justify-between gap-4">
			<div className="min-w-0 flex-1">
				<h1 className="text-foreground text-xl font-semibold tracking-tight md:text-2xl">
					{title}
				</h1>
				{subtitle && <p className="text-muted-foreground mt-0.5 text-sm">{subtitle}</p>}
			</div>
			{actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
		</div>
	);

	return (
		<div
			className={cn(
				// `-mx-page-gutter -mt-6` cancels PageShell's gutter + py-6 so
				// the band reaches the viewport edge; the matching gutter on
				// the inner div re-insets the title to align with body content.
				// No bottom margin here — PageShell's `space-y-6` owns the gap
				// to the next child, so adding one would asymmetrically grow it.
				'border-border/50 from-card to-background -mx-page-gutter -mt-6 border-b bg-gradient-to-b',
				className,
			)}
		>
			{/*
			 * Asymmetric vertical padding — `pt-4 md:pt-5` keeps a comfortable
			 * top breathing room under the fixed TopNavbar, while the smaller
			 * `pb-3` shrinks the visual gap to whatever sits flush below
			 * (e.g. Discover's sticky search toolbar). When `pb` matched `pt`
			 * the title-to-toolbar gap looked top-heavy because the toolbar's
			 * own `py-3` then stacked against a 20px header skirt.
			 */}
			<div className="px-page-gutter pt-4 pb-3 md:pt-5">
				{animated ? (
					<motion.div
						initial={{ opacity: 0, y: -8 }}
						animate={{ opacity: 1, y: 0 }}
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
