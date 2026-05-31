import React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export type LoadingStateSize = 'sm' | 'md' | 'lg';
export type LoadingStateVariant = 'default' | 'primary' | 'muted';

interface LoadingStateProps {
	message?: string;
	description?: string;
	icon?: React.ReactNode;
	size?: LoadingStateSize;
	variant?: LoadingStateVariant;
	centered?: boolean;
	className?: string;
}

const SIZE_CLASSES: Record<LoadingStateSize, string> = {
	sm: 'h-4 w-4',
	md: 'h-6 w-6',
	lg: 'h-8 w-8',
};

const VARIANT_CLASSES: Record<LoadingStateVariant, string> = {
	default: 'text-foreground',
	primary: 'text-primary',
	muted: 'text-muted-foreground',
};

export function LoadingState({
	message,
	description,
	icon,
	size = 'md',
	variant = 'muted',
	centered = true,
	className,
}: LoadingStateProps) {
	const spinner = icon ?? (
		<Loader2 className={cn('animate-spin', SIZE_CLASSES[size], VARIANT_CLASSES[variant])} />
	);

	return (
		<div
			role="status"
			aria-live="polite"
			className={cn(
				'flex flex-col items-center justify-center text-center',
				centered && 'py-16',
				className,
			)}
		>
			<div className="mb-3">{spinner}</div>
			{message && <p className="text-muted-foreground text-sm">{message}</p>}
			{description && <p className="text-muted-foreground/70 mt-1 text-xs">{description}</p>}
		</div>
	);
}
