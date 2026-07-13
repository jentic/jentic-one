import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

/**
 * CardRow — the single row layout shared by every dashboard list card
 * (pending agents, pending access requests, alerts). Keeping one structure
 * here is what makes the three cards read as one family instead of three
 * differently-styled lists: a leading visual, a title + subtitle stack, and a
 * trailing meta/action cluster, all with identical spacing and hover.
 *
 * Rows are FULL-BLEED: they carry their own `px-5` so the hover background, the
 * row content, and the `divide-y` separators between rows all align to exactly
 * the same left/right edges (the parent CardBody must drop its own padding —
 * `className="px-0 py-0"`). Every row is a real button: pointer cursor, keyboard
 * focusable, and clickable across its whole width.
 */

interface CardRowProps {
	/** Leading visual — an AgentBadge, a neutral icon medallion, etc. */
	leading?: ReactNode;
	/** Primary line. */
	title: ReactNode;
	/** Secondary line under the title. */
	subtitle?: ReactNode;
	/** Trailing meta (e.g. a timestamp) shown before the action affordance. */
	meta?: ReactNode;
	/** Action label shown at the very end (defaults to "Review"). */
	action?: ReactNode;
	/** The whole-row click handler. */
	onClick: () => void;
	className?: string;
	'aria-label'?: string;
}

export function CardRow({
	leading,
	title,
	subtitle,
	meta,
	action = 'Review',
	onClick,
	className,
	'aria-label': ariaLabel,
}: CardRowProps) {
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={ariaLabel}
			className={cn(
				'group hover:bg-muted/40 focus-visible:ring-primary/40 flex w-full cursor-pointer items-center gap-3 px-5 py-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset',
				className,
			)}
		>
			{leading && <span className="shrink-0">{leading}</span>}
			<span className="min-w-0 flex-1">
				<span className="text-foreground block truncate leading-tight font-medium">
					{title}
				</span>
				{subtitle && (
					<span className="text-muted-foreground mt-1 block truncate text-xs leading-tight">
						{subtitle}
					</span>
				)}
			</span>
			{meta && (
				<span className="text-muted-foreground hidden shrink-0 text-xs whitespace-nowrap tabular-nums sm:block">
					{meta}
				</span>
			)}
			<span className="text-muted-foreground group-hover:text-primary flex shrink-0 items-center gap-0.5 text-sm font-medium transition-colors">
				{action}
				<ChevronRight
					className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
					aria-hidden="true"
				/>
			</span>
		</button>
	);
}

/**
 * Neutral medallion for a card-header icon. We deliberately use ONE calm,
 * monochrome treatment for every card title rather than a different accent
 * colour per card — the cards are an information surface, not a set of
 * call-to-action tiles, so a rainbow of header chips just adds noise.
 */
export function CardHeaderIcon({ children }: { children: ReactNode }) {
	return (
		<span className="bg-muted text-muted-foreground ring-border flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1">
			{children}
		</span>
	);
}
