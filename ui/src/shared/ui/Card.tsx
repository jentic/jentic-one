import React from 'react';
import { cn } from '@/shared/lib/utils';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
	/** Clickable card: lightens and lifts a soft shadow on hover (no movement). */
	hoverable?: boolean;
	/**
	 * Opt back into a stated edge — for a card that sits on a surface of the
	 * same tone (inside a dialog or panel) or a dashed empty state.
	 */
	outlined?: boolean;
	/** The card's detail is open elsewhere (a sheet): an accent ring marks it. */
	selected?: boolean;
}

/**
 * A tonal surface one step lighter than the page — raised by lightening, not
 * by a border or a shadow. Fields and chips inside it step up one more tone
 * automatically (`--field-bg`).
 */
export function Card({
	hoverable,
	outlined,
	selected,
	children,
	className,
	onClick,
	...props
}: CardProps) {
	return (
		<div
			data-selected={selected || undefined}
			className={cn(
				'bg-surface-1 overflow-hidden rounded-lg [outline:1px_solid_hsl(var(--card-edge))] [outline-offset:-1px] [--field-bg:var(--surface-field)]',
				outlined && 'border-border/60 border',
				hoverable && 'card-hover cursor-pointer',
				selected && 'shadow-[0_0_0_1.5px_hsl(var(--primary)/0.45)]',
				className,
			)}
			onClick={onClick}
			{...props}
		>
			{children}
		</div>
	);
}

interface CardSectionProps {
	children: React.ReactNode;
	className?: string;
	/** Draw a hairline between this band and the body (off by default). */
	divider?: boolean;
}

export function CardHeader({ children, className, divider }: CardSectionProps) {
	return (
		<div className={cn('px-5 py-4', divider && 'border-hairline border-b', className)}>
			{children}
		</div>
	);
}

export function CardBody({
	children,
	className,
}: {
	children: React.ReactNode;
	className?: string;
}) {
	return <div className={cn('px-5 py-4', className)}>{children}</div>;
}

export function CardFooter({ children, className, divider }: CardSectionProps) {
	return (
		<div className={cn('px-5 py-4', divider && 'border-hairline border-t', className)}>
			{children}
		</div>
	);
}

export function CardTitle({
	children,
	className,
	as: Tag = 'h3',
}: {
	children: React.ReactNode;
	className?: string;
	/** Heading level. Defaults to `h3`; use `h2` for top-level page sections. */
	as?: 'h2' | 'h3' | 'h4';
}) {
	return (
		<Tag className={cn('font-heading text-foreground-name font-semibold', className)}>
			{children}
		</Tag>
	);
}
