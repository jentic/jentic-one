import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

export interface BannerProps {
	/** Banner body — a short message, optionally with an inline action link. */
	children: ReactNode;
	/** Called when the user dismisses the banner. When omitted, no close button. */
	onDismiss?: () => void;
	/** Accessible label for the dismiss button. */
	dismissLabel?: string;
	className?: string;
}

/**
 * A slim, dismissible app-shell banner (e.g. "an update is available").
 *
 * Presentational only — the caller owns visibility and any persistence. Uses a
 * polite live region (`role="status"` / `aria-live="polite"`) rather than the
 * assertive `role="alert"`: this is a non-urgent, persistent notice, so it must
 * not interrupt a screen-reader user mid-task.
 */
export function Banner({ children, onDismiss, dismissLabel = 'Dismiss', className }: BannerProps) {
	return (
		<div
			role="status"
			aria-live="polite"
			className={cn(
				'border-primary/30 bg-primary/10 text-foreground flex items-center gap-3 border-b px-4 py-2 text-sm',
				className,
			)}
		>
			<div className="min-w-0 flex-1">{children}</div>
			{onDismiss && (
				<button
					type="button"
					onClick={onDismiss}
					aria-label={dismissLabel}
					className="text-muted-foreground hover:text-foreground shrink-0 rounded p-0.5 transition-colors"
				>
					<X className="h-4 w-4" aria-hidden="true" />
				</button>
			)}
		</div>
	);
}
