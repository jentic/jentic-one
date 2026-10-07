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
	// Mirrors `open` for the native `close` listener; updated before the
	// show/close effect below so a `close` it triggers sees the new value.
	const openRef = useRef(open);

	useEffect(() => {
		openRef.current = open;
		const dialog = dialogRef.current;
		if (!dialog) return;

		if (open && !dialog.open) {
			dialog.showModal();
		} else if (!open && dialog.open) {
			dialog.close();
		}
	}, [open]);

	// The browser can close a modal on its own (Esc without a `cancel` event, per
	// the close-watcher rules): keep the owner's `open` in step when it does. A
	// `close` the effect above triggers (owner already set `open` false), or one
	// that lands while the element is open again, is not a native dismissal.
	const handleNativeClose = useCallback(() => {
		if (openRef.current && !dialogRef.current?.open) onClose();
	}, [onClose]);

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
			onClose={handleNativeClose}
			onClick={handleBackdropClick}
			className={cn(
				// Same surface grammar as a sheet — a tinted blurred backdrop, fields
				// one step lighter, a darker footer band instead of a rule — plus a
				// faint hairline edge on the panel (the same edge as form fields).
				// `text-foreground` restores the app's text colour: a modal `<dialog>`
				// sits in the top layer with the UA's `color: CanvasText`, so any text
				// without its own colour class would otherwise render near-black.
				'bg-surface-sheet text-foreground shadow-pop rounded-panel border-hairline-field m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-hidden border p-0 [--field-bg:var(--surface-field)] backdrop:bg-[hsl(192_35%_4%/0.55)] backdrop:backdrop-blur-[3px] sm:w-full',
				'overscroll-contain',
				// A gentle scale/fade entrance. A closed `<dialog>` is `display: none`,
				// so the animation replays on every `showModal()`. Under reduced motion
				// the global reset in index.css cuts it (and the `::backdrop` fade) to a
				// near-instant frame.
				'animate-dialog-in',
				// Smoothly grow/shrink when the size prop changes between steps
				// (e.g. the credential wizard widening from lg → xl) instead of
				// snapping; `motion-reduce:` switches it off.
				'transition-[max-width] duration-300 ease-out motion-reduce:transition-none',
				sizeClasses[size],
				className,
			)}
		>
			<div className="flex max-h-[calc(100dvh-2rem)] flex-col">
				<div className="flex shrink-0 items-start justify-between gap-3 px-5 pt-5 pb-3">
					<div className="min-w-0 flex-1">
						<h2
							id={titleId}
							className="font-heading text-lg leading-tight font-semibold text-white/92"
						>
							{title}
						</h2>
						{subtitle && (
							<div className="text-foreground-sub mt-1 text-[13px]">{subtitle}</div>
						)}
					</div>
					<Button
						variant="ghost"
						size="icon"
						onClick={onClose}
						aria-label="Close"
						className="-mt-1 -mr-1.5"
					>
						<X className="h-4.5 w-4.5" />
					</Button>
				</div>
				<div className="overflow-y-auto px-5 py-4">{children}</div>
				{footer && (
					<div className="bg-surface-sheet-foot border-hairline-field flex shrink-0 items-center justify-end gap-2 border-t px-5 py-3.5">
						{footer}
					</div>
				)}
			</div>
		</dialog>
	);
}
