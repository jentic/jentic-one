import React from 'react';
import { cn } from '@/shared/lib/utils';

export type Variant = 'default' | 'success' | 'warning' | 'danger' | 'pending';

const variantClasses: Record<Variant, string> = {
	default: 'bg-primary/12 text-primary border-primary/25',
	success: 'bg-success/12 text-success border-success/25',
	warning: 'bg-warning/15 text-warning border-warning/30',
	danger: 'bg-danger/12 text-danger border-danger/25',
	pending: 'bg-accent-orange/12 text-accent-orange border-accent-orange/25',
};

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
	variant?: Variant;
	/** Show a leading status dot in the badge's colour. */
	dot?: boolean;
}

export function Badge({ variant = 'default', dot, children, className, ...props }: BadgeProps) {
	return (
		<span
			className={cn(
				'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-xs font-medium tabular-nums',
				variantClasses[variant],
				className,
			)}
			{...props}
		>
			{dot && (
				<span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" />
			)}
			{children}
		</span>
	);
}

const methodColors: Record<string, string> = {
	GET: 'bg-accent-teal/10 text-accent-teal border-accent-teal/30',
	POST: 'bg-accent-blue/10 text-accent-blue border-accent-blue/30',
	PUT: 'bg-accent-orange/10 text-accent-orange border-accent-orange/30',
	PATCH: 'bg-accent-yellow/10 text-accent-yellow border-accent-yellow/30',
	DELETE: 'bg-danger/10 text-danger border-danger/30',
};

export function MethodBadge({ method }: { method?: string | null }) {
	const m = method?.toUpperCase() ?? '?';
	const colors = methodColors[m] ?? 'bg-muted text-muted-foreground border-border';
	return (
		<span
			className={cn(
				// Fixed width + no shrink so the following path never shifts as the
				// method label changes (GET vs DELETE) down a list of operations.
				'inline-flex w-14 shrink-0 items-center justify-center rounded border px-1 py-0.5 text-center font-mono text-[10px] font-bold',
				colors,
			)}
		>
			{m}
		</span>
	);
}

export function StatusBadge({ status }: { status?: number | null }) {
	if (!status) return null;
	const variant: Variant =
		status >= 500
			? 'danger'
			: status >= 400
				? 'warning'
				: status >= 200 && status < 300
					? 'success'
					: 'default';
	return <Badge variant={variant}>{status}</Badge>;
}
