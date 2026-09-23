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

export type StatusTone = 'success' | 'warning' | 'muted';

const statusToneClasses: Record<StatusTone, { text: string; dot: string }> = {
	success: { text: 'text-success', dot: 'bg-success ring-3 ring-success/20' },
	warning: { text: 'text-warning', dot: 'bg-warning ring-3 ring-warning/20' },
	muted: { text: 'text-muted-foreground', dot: 'border border-current' },
};

interface StatusTextProps extends React.HTMLAttributes<HTMLSpanElement> {
	tone: StatusTone;
}

/**
 * A live state as a dot and a word, without a container — for a card's one
 * status line, where a filled pill competes with the card's own title. A muted
 * tone draws a hollow dot: the thing is idle, not faulty.
 */
export function StatusText({ tone, children, className, ...props }: StatusTextProps) {
	const classes = statusToneClasses[tone];
	return (
		<span
			className={cn(
				'inline-flex items-center gap-2 text-xs font-medium',
				classes.text,
				className,
			)}
			{...props}
		>
			<span
				className={cn('h-1.5 w-1.5 shrink-0 rounded-full', classes.dot)}
				aria-hidden="true"
			/>
			{children}
		</span>
	);
}

interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
	/** Optional leading glyph that names the category at a glance. */
	icon?: React.ComponentType<{ className?: string }>;
}

/**
 * A neutral category label (an auth type, a kind) — grey, because a category
 * is neither good nor bad, so it takes none of the status colours.
 */
export function Tag({ icon: Icon, children, className, ...props }: TagProps) {
	return (
		<span
			className={cn(
				'bg-muted/60 text-muted-foreground border-border/60 inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] leading-none font-medium whitespace-nowrap',
				className,
			)}
			{...props}
		>
			{Icon && <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />}
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
