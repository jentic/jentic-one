import { useEffect, useRef, useState, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Button } from '@/shared/ui/Button';
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
 * Full-bleed page header band.
 *
 * It escapes the gutter padding that `PageShell` applies by using
 * negative margins, so the gradient band stretches edge-to-edge.
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
				<div className="flex shrink-0 items-center gap-2 self-center">{actions}</div>
			)}
		</div>
	);

	return (
		<div
			className={cn(
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
