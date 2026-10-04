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
				'bg-surface-1 animate-rise flex flex-col items-center justify-center rounded-lg p-6 text-center sm:p-10',
				className,
			)}
		>
			<div className="text-primary bg-surface-tonal mb-4 flex h-12 w-12 items-center justify-center rounded-full">
				{icon}
			</div>
			<p className="font-heading text-foreground-name text-base font-semibold">{title}</p>
			{description && (
				<p className="text-muted-foreground mt-1.5 max-w-sm text-sm leading-relaxed">
					{description}
				</p>
			)}
			{action && <div className="mt-5">{action}</div>}
		</div>
	);
}
