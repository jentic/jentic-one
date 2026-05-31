import { useEffect } from 'react';
import { X } from 'lucide-react';
import { Button } from './Button';
import { dismissToast, useToasts, type ToastEntry } from './toastStore';

/**
 * Mounted once at the root layout. Subscribes to the toast store and
 * renders a stack of `ToastView`s in the bottom-right corner.
 */
export function Toaster() {
	const toasts = useToasts();
	if (toasts.length === 0) return null;

	return (
		<div
			data-testid="toaster"
			className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-full max-w-sm flex-col gap-2"
			aria-live="polite"
			aria-atomic="false"
		>
			{toasts.map((t) => (
				<ToastView key={t.id} entry={t} />
			))}
		</div>
	);
}

function ToastView({ entry }: { entry: ToastEntry }) {
	useEffect(() => {
		const id = window.setTimeout(() => dismissToast(entry.id), entry.durationMs);
		return () => window.clearTimeout(id);
	}, [entry.id, entry.durationMs]);

	const variantClass: Record<ToastEntry['variant'], string> = {
		default: 'border-border/60 bg-card',
		success: 'border-emerald-500/30 bg-emerald-500/[0.08]',
		error: 'border-rose-500/30 bg-rose-500/[0.08]',
	};

	return (
		<div
			role="status"
			data-testid="toast"
			data-variant={entry.variant}
			className={`pointer-events-auto flex items-start gap-3 rounded-lg border p-3 shadow-lg shadow-black/[0.06] backdrop-blur ${variantClass[entry.variant]}`}
		>
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm font-medium">{entry.title}</p>
				{entry.description && (
					<p className="text-muted-foreground mt-0.5 text-xs">{entry.description}</p>
				)}
				{entry.action && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={() => {
							entry.action?.onClick();
							dismissToast(entry.id);
						}}
						className="text-primary hover:text-primary/80 mt-1.5 h-auto px-1 py-0 text-xs font-medium"
					>
						{entry.action.label}
					</Button>
				)}
			</div>
			<Button
				type="button"
				variant="ghost"
				size="icon"
				onClick={() => dismissToast(entry.id)}
				aria-label="Dismiss"
				className="text-muted-foreground hover:text-foreground h-6 w-6 shrink-0 rounded p-0.5"
			>
				<X className="h-3.5 w-3.5" />
			</Button>
		</div>
	);
}
