import React from 'react';
import { cn } from '@/lib/utils';

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
				'bg-muted border-border flex flex-col items-center justify-center rounded-xl border p-6 text-center sm:p-12',
				className,
			)}
		>
			<div className="text-muted-foreground mb-4">{icon}</div>
			<p className="text-foreground text-lg font-semibold">{title}</p>
			{description && (
				<p className="text-muted-foreground mt-2 max-w-sm text-sm">{description}</p>
			)}
			{action && <div className="mt-6">{action}</div>}
		</div>
	);
}
