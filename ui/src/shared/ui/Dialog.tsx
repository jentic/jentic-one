import React, { useRef, useEffect, useCallback } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

/**
 * Native `<dialog>`-backed modal primitive.
 *
 * State lifecycle: **reset on successful commit, persist between
 * dismissals.** Owners decide when to reset form state. Prefer resetting
 * inside the success path of the submit handler. Because this primitive
 * exposes no post-close hook, multi-step wizards that must fully reset on
 * dismissal may instead watch `open` flipping to `false` and reset there —
 * that's an accepted pattern, not an anti-pattern. Always clear transient
 * flags (`submitting`, `error`) when reopening, and clear sensitive fields
 * (passwords, API keys, OTPs) on every dismissal.
 */
type DialogSize = 'sm' | 'md' | 'lg' | 'xl';

const sizeClasses: Record<DialogSize, string> = {
	sm: 'max-w-sm',
	md: 'max-w-lg',
	lg: 'max-w-2xl',
	xl: 'max-w-3xl',
};

interface DialogProps {
	open: boolean;
	onClose: () => void;
	title: string;
	/**
	 * Optional secondary line under the title — use for step indicators
	 * ("Step 1 of 2 · Choose an API"), short context strings, or breadcrumbs.
	 * Kept as a React node so callers can compose icons/badges.
	 */
	subtitle?: React.ReactNode;
	children: React.ReactNode;
	footer?: React.ReactNode;
	size?: DialogSize;
	className?: string;
	/**
	 * If `false`, clicking the backdrop will NOT close the dialog —
	 * Escape and the explicit X / Cancel still close. Default `true`.
	 */
	dismissOnBackdrop?: boolean;
	/**
	 * Optional id of an element that *describes* (vs. names) the dialog —
	 * wired through to `aria-describedby`.
	 */
	describedById?: string;
}

export function Dialog({
	open,
	onClose,
	title,
	subtitle,
	children,
	footer,
	size = 'md',
	className,
	dismissOnBackdrop = true,
	describedById,
}: DialogProps) {
	const dialogRef = useRef<HTMLDialogElement>(null);
	const titleId = `dialog-title-${React.useId()}`;

	useEffect(() => {
		const dialog = dialogRef.current;
		if (!dialog) return;

		if (open && !dialog.open) {
			dialog.showModal();
		} else if (!open && dialog.open) {
			dialog.close();
		}
	}, [open]);

	const handleCancel = useCallback(
		(e: React.SyntheticEvent<HTMLDialogElement>) => {
			e.preventDefault();
			onClose();
		},
		[onClose],
	);

	const handleBackdropClick = useCallback(
		(e: React.MouseEvent<HTMLDialogElement>) => {
			if (!dismissOnBackdrop) return;
			if (e.target === dialogRef.current) {
				onClose();
			}
		},
		[onClose, dismissOnBackdrop],
	);

	return (
		<dialog
			ref={dialogRef}
			aria-labelledby={titleId}
			aria-describedby={describedById}
			onCancel={handleCancel}
			onClick={handleBackdropClick}
			className={cn(
				'bg-card border-border shadow-pop m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-hidden rounded-xl border p-0 backdrop:bg-black/60 backdrop:backdrop-blur-[2px] sm:w-full',
				'overscroll-contain',
				// A gentle scale/fade entrance (open) — fast, subtle, and disabled
				// under reduced-motion via the global media reset.
				'open:animate-dialog-in',
				// Smoothly grow/shrink when the size prop changes between steps
				// (e.g. the credential wizard widening from lg → xl) instead of
				// snapping. Respects reduced-motion via the global media reset.
				'transition-[max-width] duration-300 ease-out motion-reduce:transition-none',
				sizeClasses[size],
				className,
			)}
		>
			<div className="flex max-h-[calc(100dvh-2rem)] flex-col">
				<div className="border-border flex shrink-0 items-start justify-between gap-3 border-b px-5 py-4">
					<div className="min-w-0 flex-1">
						<h2 id={titleId} className="text-foreground text-lg font-semibold">
							{title}
						</h2>
						{subtitle && (
							<div className="text-muted-foreground mt-0.5 text-xs">{subtitle}</div>
						)}
					</div>
					<Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
						<X className="h-5 w-5" />
					</Button>
				</div>
				<div className="overflow-y-auto px-5 py-4">{children}</div>
				{footer && (
					<div className="border-border flex shrink-0 items-center justify-end gap-2 border-t px-5 py-4">
						{footer}
					</div>
				)}
			</div>
		</dialog>
	);
}
