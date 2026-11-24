/**
 * Layout primitives for the held-call review page — a labelled section and a
 * label/value row, in the same look as Monitor's execution trace detail so a
 * held call reads like the call it becomes once approved.
 */
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

export function ReviewSection({
	title,
	action,
	children,
	className,
}: {
	title: string;
	action?: ReactNode;
	children: ReactNode;
	className?: string;
}) {
	return (
		<section className={cn('space-y-2', className)} aria-label={title}>
			<div className="flex items-center justify-between gap-2">
				<h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
					{title}
				</h2>
				{action}
			</div>
			{children}
		</section>
	);
}

/** A rounded panel on the page surface — the trace detail's call card. */
export function ReviewPanel({ children, className }: { children: ReactNode; className?: string }) {
	return <div className={cn('bg-field rounded-lg p-4', className)}>{children}</div>;
}
