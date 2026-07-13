import React from 'react';
import { cn } from '@/shared/lib/utils';

interface EmptyStateProps {
	icon: React.ReactNode;
	title: string;
	description?: string;
	action?: React.ReactNode;
	className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
	return (
		<div
			className={cn(
				'border-border/70 from-muted/60 to-card animate-rise flex flex-col items-center justify-center rounded-xl border border-dashed bg-gradient-to-b p-6 text-center sm:p-10',
				className,
			)}
		>
			<div className="text-primary/80 ring-primary/15 bg-primary/5 mb-4 flex h-14 w-14 items-center justify-center rounded-full ring-1">
				{icon}
			</div>
			<p className="font-heading text-foreground text-base font-semibold">{title}</p>
			{description && (
				<p className="text-muted-foreground mt-1.5 max-w-sm text-sm leading-relaxed">
					{description}
				</p>
			)}
			{action && <div className="mt-5">{action}</div>}
		</div>
	);
}
