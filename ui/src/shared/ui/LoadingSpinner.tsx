import { type JSX } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

export type LoadingSpinnerSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';
export type LoadingSpinnerVariant = 'default' | 'primary' | 'muted';

export interface LoadingSpinnerProps {
	size?: LoadingSpinnerSize;
	variant?: LoadingSpinnerVariant;
	text?: string;
	description?: string;
	className?: string;
	centered?: boolean;
}

const SIZE_CLASSES: Record<LoadingSpinnerSize, string> = {
	xs: 'h-4 w-4',
	sm: 'h-6 w-6',
	md: 'h-8 w-8',
	lg: 'h-10 w-10',
	xl: 'h-12 w-12',
};

const VARIANT_CLASSES: Record<LoadingSpinnerVariant, string> = {
	default: 'text-foreground',
	primary: 'text-primary',
	muted: 'text-muted-foreground',
};

export function LoadingSpinner({
	size = 'md',
	variant = 'muted',
	text,
	description,
	className,
	centered = false,
}: LoadingSpinnerProps): JSX.Element {
	const spinner = (
		<Loader2
			className={cn('animate-spin', SIZE_CLASSES[size], VARIANT_CLASSES[variant], className)}
		/>
	);

	if (!text && !description) {
		if (centered) {
			return <div className="flex flex-1 items-center justify-center">{spinner}</div>;
		}
		return spinner;
	}

	const content = (
		<div className="flex flex-col items-center space-y-2">
			{spinner}
			{text && <h3 className="text-foreground text-lg font-medium">{text}</h3>}
			{description && <p className="text-muted-foreground text-sm">{description}</p>}
		</div>
	);

	if (centered) {
		return <div className="flex flex-1 items-center justify-center">{content}</div>;
	}

	return content;
}
