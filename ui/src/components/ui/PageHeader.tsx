import { useEffect, useRef, useState, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/utils';

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
	icon,
	actions,
	animated = true,
	className,
}: PageHeaderProps) {
	const [expanded, setExpanded] = useState(false);
	const [clamped, setClamped] = useState(false);
	const subtitleRef = useRef<HTMLParagraphElement | null>(null);

	useEffect(() => {
		const el = subtitleRef.current;
		if (!el) return;
		const check = () => setClamped(el.scrollHeight > el.clientHeight);
		check();
		const ro = new ResizeObserver(check);
		ro.observe(el);
		return () => ro.disconnect();
	}, [subtitle, expanded]);

	const content = (
		<div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
			<div className="flex min-w-0 basis-full items-start gap-3 sm:flex-1 sm:basis-0">
				{icon && <div className="shrink-0">{icon}</div>}
				<div className="min-w-0 flex-1">
					<h1 className="text-foreground text-xl font-semibold tracking-tight md:text-2xl">
						{title}
					</h1>
					{subtitle && (
						<div className="mt-0.5">
							<p
								ref={subtitleRef}
								className={cn(
									'text-muted-foreground text-sm',
									!expanded && 'line-clamp-2',
								)}
							>
								{subtitle}
							</p>
							{(clamped || expanded) && (
								<Button
									variant="ghost"
									size="sm"
									onClick={() => setExpanded((v) => !v)}
									className="text-muted-foreground hover:text-foreground mt-0.5 h-auto px-0 py-0 text-xs font-medium"
								>
									{expanded ? 'Show less' : 'Show more'}
								</Button>
							)}
						</div>
					)}
				</div>
			</div>
			{actions && (
				// `self-center` keeps the actions cluster vertically centred
				// against the title row when nothing wraps. The parent uses
				// `items-start` so a multi-line subtitle hugs the top of the
				// title column; without `self-center` here the actions would
				// also pin to the top and look top-heavy on the most common
				// (single-line) header layout. When the row wraps to a new
				// line on narrow viewports `self-center` is a no-op (it
				// centres within its own flex line).
				<div className="flex shrink-0 items-center gap-2 self-center">{actions}</div>
			)}
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
			<div className="px-4 py-4 md:py-5">
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
