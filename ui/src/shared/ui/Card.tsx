import React from 'react';
import { cn } from '@/shared/lib/utils';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
	hoverable?: boolean;
}

export function Card({ hoverable, children, className, onClick, ...props }: CardProps) {
	return (
		<div
			className={cn(
				'bg-card border-border shadow-card overflow-hidden rounded-xl border',
				hoverable &&
					'card-lift hover:border-primary/50 hover:shadow-card-hover hover:bg-muted/40 cursor-pointer',
				className,
			)}
			onClick={onClick}
			{...props}
		>
			{children}
		</div>
	);
}

export function CardHeader({
	children,
	className,
}: {
	children: React.ReactNode;
	className?: string;
}) {
	return <div className={cn('border-border border-b px-5 py-4', className)}>{children}</div>;
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

export function CardFooter({
	children,
	className,
}: {
	children: React.ReactNode;
	className?: string;
}) {
	return <div className={cn('border-border border-t px-5 py-4', className)}>{children}</div>;
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
		<Tag className={cn('font-heading text-foreground font-semibold', className)}>
			{children}
		</Tag>
	);
}
