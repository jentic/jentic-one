import type { ReactNode } from 'react';
import { AlertTriangle, ArrowUpRight } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import { Card, Skeleton, AppLink } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

/** Accent tone for a tile — drives the icon medallion + hairline glow. */
export type StatAccent = 'neutral' | 'primary' | 'orange' | 'green' | 'blue' | 'pink';

const ACCENT_MEDALLION: Record<StatAccent, string> = {
	neutral: 'bg-muted text-muted-foreground ring-border',
	primary: 'bg-primary/12 text-primary ring-primary/20',
	orange: 'bg-accent-orange/12 text-accent-orange ring-accent-orange/20',
	green: 'bg-accent-green/12 text-accent-green ring-accent-green/20',
	blue: 'bg-accent-blue/12 text-accent-blue ring-accent-blue/20',
	pink: 'bg-accent-pink/12 text-accent-pink ring-accent-pink/20',
};

interface StatCardProps {
	label: string;
	/** The headline value (already formatted). Ignored while loading/error. */
	value: ReactNode;
	icon?: ReactNode;
	/** A small caption under the value (e.g. "of 25 sampled"). */
	caption?: string;
	/** Accent tone for the icon medallion. Defaults to `neutral`. */
	accent?: StatAccent;
	/**
	 * When set, the whole tile becomes a router link to this client route — the
	 * count is a jump-off into the surface that owns it (e.g. "Awaiting
	 * approval" → /agents). Tiles without a natural destination omit it.
	 */
	href?: string;
	isLoading?: boolean;
	/** When set, the card shows a compact degraded state instead of the value. */
	error?: string | null;
	className?: string;
}

/**
 * One headline tile in the overview grid. Each tile owns its own
 * loading/error state because the four sources are fetched independently —
 * one failing endpoint degrades only its tile, never the whole page.
 *
 * The layout is deliberately COMPACT: label + icon on one line, then the value
 * and its caption tucked right beneath — no stretched-out empty middle. When a
 * tile has a `href`, the whole card is a link (pointer cursor, focus ring, a
 * corner arrow on hover) so the headline number is a real jump-off, not just a
 * stat that looks clickable but isn't.
 *
 * The value animates in on first paint (a soft rise) so a freshly-loaded or
 * just-invalidated count reads as "updated" without being distracting.
 */
export function StatCard({
	label,
	value,
	icon,
	caption,
	accent = 'neutral',
	href,
	isLoading,
	error,
	className,
}: StatCardProps) {
	const prefersReducedMotion = useReducedMotion();
	const clickable = Boolean(href) && !isLoading && !error;

	const body = (
		<div className="relative flex flex-col gap-2.5 p-4 sm:p-5">
			<div className="flex items-start justify-between gap-2">
				<span className="text-muted-foreground font-mono text-[11px] leading-none font-medium tracking-wider uppercase">
					{label}
				</span>
				{icon && (
					<span
						className={cn(
							'-mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ring-1',
							ACCENT_MEDALLION[accent],
						)}
					>
						{icon}
					</span>
				)}
			</div>

			{isLoading ? (
				<Skeleton className="h-9 w-20" />
			) : error ? (
				<div role="alert" className="text-danger flex items-center gap-1.5 text-sm">
					<AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
					<span className="truncate">{error}</span>
				</div>
			) : (
				<div className="flex items-baseline gap-2">
					<motion.span
						key={String(value)}
						initial={prefersReducedMotion ? false : { y: 6 }}
						animate={{ y: 0 }}
						transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
						className="font-heading text-foreground text-3xl leading-none font-bold tabular-nums"
					>
						{value}
					</motion.span>
					{caption && (
						<span className="text-muted-foreground truncate text-xs">{caption}</span>
					)}
				</div>
			)}

			{/* Hover affordance sits directly UNDER the icon (bottom-right), so the
			    eye travels icon → arrow without crowding the label row. */}
			{clickable && (
				<ArrowUpRight
					className="text-muted-foreground absolute right-4 bottom-4 h-4 w-4 shrink-0 opacity-0 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:opacity-100 sm:right-5 sm:bottom-5"
					aria-hidden="true"
				/>
			)}
		</div>
	);

	if (clickable && href) {
		return (
			<AppLink href={href} className="group block" aria-label={label}>
				<Card hoverable className={cn('h-full', className)}>
					{body}
				</Card>
			</AppLink>
		);
	}

	return <Card className={cn('h-full', className)}>{body}</Card>;
}
