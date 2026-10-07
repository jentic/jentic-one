import { useEffect } from 'react';
import { AlertTriangle, CheckCircle2, Info, X, XCircle, type LucideIcon } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';
import { dismissToast, useToasts, type ToastEntry } from '@/shared/ui/toastStore';

/**
 * The one toast surface (platform `Toaster` and the agent-stream `ToastHost`):
 * a calm neutral card — the sheet surface, the hairline edge every floating
 * panel (Dialog) uses, and the shared pop shadow. Colour never fills the card
 * or its text; it rides on the small leading glyph only, and an error adds a
 * thin red bar on its left edge so it stays identifiable at a glance.
 */
export const toastSurfaceClass =
	'bg-surface-sheet border-hairline-field shadow-pop pointer-events-auto relative overflow-hidden rounded-lg border p-3 pl-3.5';

/** The thin left accent for failures (`role` stays on the card). */
export function ToastAccentBar({ className }: { className?: string }) {
	return (
		<span
			aria-hidden="true"
			data-testid="toast-accent-bar"
			className={cn(
				'bg-danger/80 absolute inset-y-2 left-0 w-[3px] rounded-r-full',
				className,
			)}
		/>
	);
}

/** Glyph + muted semantic tint per toast variant. */
const TOAST_VARIANT_ICON: Record<ToastEntry['variant'], { icon: LucideIcon; tone: string }> = {
	default: { icon: Info, tone: 'text-foreground-sub' },
	info: { icon: Info, tone: 'text-primary' },
	success: { icon: CheckCircle2, tone: 'text-success/85' },
	warning: { icon: AlertTriangle, tone: 'text-warning' },
	error: { icon: XCircle, tone: 'text-danger' },
};

/**
 * Mounted once at the root layout, inside the shell's toast region (which
 * places it). Subscribes to the toast store and renders a stack of `ToastView`s,
 * newest on top.
 */
export function Toaster() {
	const toasts = useToasts();
	if (toasts.length === 0) return null;

	return (
		<div
			data-testid="toaster"
			className="flex flex-col gap-2"
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

	const { icon: Icon, tone } = TOAST_VARIANT_ICON[entry.variant];

	return (
		<div
			role="status"
			data-testid="toast"
			data-variant={entry.variant}
			className={cn(toastSurfaceClass, 'flex items-start gap-2.5')}
		>
			{entry.variant === 'error' && <ToastAccentBar />}
			<Icon
				className={cn('mt-0.5 h-4 w-4 shrink-0', tone)}
				aria-hidden="true"
				data-testid="toast-icon"
			/>
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm font-semibold">{entry.title}</p>
				{entry.description && (
					<p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
						{entry.description}
					</p>
				)}
				{entry.action && (
					<Button
						type="button"
						variant="tonal"
						size="xs"
						onClick={() => {
							entry.action?.onClick();
							dismissToast(entry.id);
						}}
						className="mt-2"
					>
						{entry.action.label}
					</Button>
				)}
			</div>
			<Button
				type="button"
				variant="ghost"
				size="icon-xs"
				onClick={() => dismissToast(entry.id)}
				aria-label="Dismiss"
				className="-mt-1 -mr-1 h-6 w-6 shrink-0"
			>
				<X className="h-3.5 w-3.5" />
			</Button>
		</div>
	);
}
