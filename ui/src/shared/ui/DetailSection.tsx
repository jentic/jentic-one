import type { ReactNode } from 'react';
import { Button } from '@/shared/ui/Button';
import { Card, CardBody, CardHeader, CardTitle } from '@/shared/ui/Card';
import { cn } from '@/shared/lib/utils';

/**
 * DetailSection — the card shell every detail-console section renders inside
 * (toolkit, agent, and service-account consoles): the shared `Card` family
 * with a header grammar (icon medallion + heading + right-slot) layered on
 * top, so section chrome stays one primitive across the product.
 *
 * Promoted from the toolkit console's `components/detail/shared.tsx` once the
 * agent console needed the identical shell (library-first rule).
 */

export interface SectionActionProps {
	label: ReactNode;
	onClick: () => void;
	variant?: 'primary' | 'secondary' | 'outline';
	/** Accessible name when the visible label alone is ambiguous. */
	ariaLabel?: string;
}

export interface DetailSectionProps {
	/** Section heading (sentence-case, `font-heading font-semibold` ladder). */
	title: ReactNode;
	/**
	 * Leading glyph for the heading (`h-4 w-4`), rendered in the same muted
	 * icon medallion the dashboard sections use — one grammar everywhere.
	 */
	icon?: ReactNode;
	/** Extra inline content next to the title (e.g. a "Keys blocked" pill). */
	titleExtra?: ReactNode;
	/** Right-aligned header action button. */
	action?: SectionActionProps;
	/** Danger-tinted borders (suspended keys, danger zone). */
	danger?: boolean;
	/** Extra classes on the card shell (e.g. `h-full` in equal-height grids). */
	className?: string;
	/** Extra classes on the body (e.g. `px-0 py-0` to host a flush table). */
	bodyClassName?: string;
	/** Right-aligned header content when `action` (a button) doesn't fit — e.g. a link. */
	trailing?: ReactNode;
	children: ReactNode;
}

export function DetailSection({
	title,
	icon,
	titleExtra,
	action,
	danger,
	className,
	bodyClassName,
	trailing,
	children,
}: DetailSectionProps) {
	return (
		<Card className={cn('flex flex-col', danger && 'border-danger/50', className)}>
			<CardHeader
				className={cn(
					'flex flex-wrap items-center justify-between gap-2 px-4 py-3.5 sm:px-5 sm:py-4',
					danger && 'border-danger/30 bg-danger/5',
				)}
			>
				<div className="flex items-center gap-2.5">
					{icon && (
						<span
							aria-hidden="true"
							className={cn(
								'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1',
								danger
									? 'bg-danger/10 text-danger ring-danger/25'
									: 'bg-muted text-muted-foreground ring-border',
							)}
						>
							{icon}
						</span>
					)}
					<CardTitle className={cn(danger && 'text-danger')}>{title}</CardTitle>
					{titleExtra}
				</div>
				{action && (
					<Button
						variant={action.variant ?? 'secondary'}
						size="sm"
						onClick={action.onClick}
						aria-label={action.ariaLabel}
					>
						{action.label}
					</Button>
				)}
				{trailing}
			</CardHeader>
			<CardBody className={cn('flex-1 space-y-2 px-4 py-3.5 sm:px-5 sm:py-4', bodyClassName)}>
				{children}
			</CardBody>
		</Card>
	);
}

interface EmptyRowProps {
	icon: ReactNode;
	children: ReactNode;
}

/** Dashed empty-state panel used inside a `DetailSection`. */
export function EmptyRow({ icon, children }: EmptyRowProps) {
	return (
		<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
			<span className="text-muted-foreground/50 mx-auto block h-6 w-6 [&>svg]:h-6 [&>svg]:w-6">
				{icon}
			</span>
			<p className="text-muted-foreground mt-2 text-sm">{children}</p>
		</div>
	);
}
