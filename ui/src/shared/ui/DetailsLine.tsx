/** DetailsLine — a muted summary line, with "Details ▾" revealing secondary info in place. */
import { useId, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { ExpandReveal } from '@/shared/ui/ExpandReveal';
import { cn } from '@/shared/lib/utils';

export interface DetailsLineProps {
	summary: ReactNode;
	/** What "Details" reveals; without it the line stands alone. */
	children?: ReactNode;
	/** A small glyph before the summary, sized by the caller (`h-3.5 w-3.5`). */
	icon?: ReactNode;
	className?: string;
	'data-testid'?: string;
}

export function DetailsLine({
	summary,
	children,
	icon,
	className,
	'data-testid': testId,
}: DetailsLineProps) {
	const [open, setOpen] = useState(false);
	const bodyId = useId();
	return (
		<div className={cn('text-muted-foreground text-xs', className)} data-testid={testId}>
			<p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 leading-5">
				{icon && (
					<span aria-hidden="true" className="inline-flex shrink-0">
						{icon}
					</span>
				)}
				<span>{summary}</span>
				{children != null && (
					<Button
						type="button"
						variant="ghost"
						size="xs"
						aria-expanded={open}
						// Only reference the details while they are mounted.
						aria-controls={open ? bodyId : undefined}
						onClick={(): void => setOpen((v) => !v)}
						className="text-foreground-sub hover:text-foreground h-5 gap-0.5 px-1 text-xs font-medium hover:bg-transparent hover:underline"
					>
						Details
						<ChevronDown
							aria-hidden="true"
							className={cn(
								'h-3 w-3 transition-transform duration-200 motion-reduce:transition-none',
								open && 'rotate-180',
							)}
						/>
					</Button>
				)}
			</p>
			{children != null && (
				<ExpandReveal open={open} id={bodyId}>
					<div className="text-foreground-sub space-y-1 pt-1.5 leading-relaxed">
						{children}
					</div>
				</ExpandReveal>
			)}
		</div>
	);
}
