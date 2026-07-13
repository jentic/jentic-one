/**
 * Small layout primitives for Monitor detail sheets — a labelled section and a
 * label/value row. Kept module-local so the detail sheets share one look.
 */
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

export function DetailSection({
	title,
	action,
	children,
}: {
	title: string;
	action?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section className="space-y-2">
			<div className="flex items-center justify-between gap-2">
				<h3 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
					{title}
				</h3>
				{action}
			</div>
			{children}
		</section>
	);
}

export function DetailRow({
	label,
	value,
	mono,
}: {
	label: string;
	value: ReactNode;
	mono?: boolean;
}) {
	return (
		<div className="mt-1.5 flex items-baseline justify-between gap-3 text-sm">
			<span className="text-muted-foreground shrink-0 text-xs">{label}</span>
			<span className={cn('min-w-0 text-right break-all', mono && 'font-mono text-xs')}>
				{value}
			</span>
		</div>
	);
}
