import React, { useRef, useEffect, useCallback } from 'react';
import { X } from 'lucide-react';
import { Button } from './Button';
import { cn } from '@/lib/utils';

/**
 * Native `<dialog>`-backed modal primitive.
 *
 * # State lifecycle (READ ME before you author a new dialog)
 *
 * Owners of `<Dialog>` decide when to *reset* the form/UI state
 * inside it. The project-wide rule is:
 *
 *   **Reset on successful commit. Persist between dismissals.**
 *
 * Concretely:
 *
 *  - Don't `useEffect(() => { if (open) reset(); }, [open])`.
 *    That clobbers a draft every time the user re-opens the dialog
 *    after a casual Esc or X dismissal — which is the *normal*
 *    interaction in this app (Esc-to-peek, re-open to continue).
 *
 *  - Don't reset in the parent's `onClose` handler either. Same
 *    problem: dismissal != commit.
 *
 *  - Reset **inside the success path of your submit handler**,
 *    immediately before calling `onClose()`. That's the only point
 *    where the draft has been turned into something durable on
 *    the server (and it's the only point where keeping it around
 *    would actually mislead the user).
 *
 *  - **Always** reset transient flags (`submitting`, `error`,
 *    `dragging`, etc.) when `open` flips to `true` — those aren't
 *    user input, they're stale state from the last attempt and
 *    they will mislead on the next one (e.g. a red error banner
 *    above an empty form).
 *
 *  - For dialogs that are seeded from props (e.g. an "Edit X"
 *    dialog that pre-fills from a `target` prop), sync the seed
 *    via a `useEffect` that watches **only the seed**, not `open`.
 *    Seeding on every open is fine when the seed itself changed;
 *    it's wrong when only `open` flipped.
 *
 *  - **Sensitive data** (passwords, API keys, OTPs, payment
 *    details) is the ONE exception: clear those fields on every
 *    dismissal, no matter what. Document the exception inline.
 *
 *  - **Pure confirmation dialogs** ("Delete this?") have nothing
 *    to preserve, so the rule is moot.
 *
 * See `dialog-state-lifecycle.mdc` (Cursor rule) for the same
 * guidance with examples.
 *
 * # Props
 *
 *  - `dismissOnBackdrop` — default `true`. Set to `false` for
 *    flows where a misclick on the backdrop would lose work
 *    (e.g. half-pasted JSON in an import flow). Esc and the X
 *    still close in that case — only the backdrop is muted.
 */
type DialogSize = 'sm' | 'md' | 'lg';

const sizeClasses: Record<DialogSize, string> = {
	sm: 'max-w-sm',
	md: 'max-w-lg',
	lg: 'max-w-2xl',
};

interface DialogProps {
	open: boolean;
	onClose: () => void;
	title: string;
	children: React.ReactNode;
	footer?: React.ReactNode;
	size?: DialogSize;
	className?: string;
	/**
	 * If `false`, clicking the backdrop will NOT close the dialog —
	 * Escape and the explicit X / Cancel still close. Default `true`
	 * to preserve the existing behaviour for every dialog that hasn't
	 * opted out.
	 */
	dismissOnBackdrop?: boolean;
	/**
	 * Optional id of an element that *describes* (vs. names) the
	 * dialog — wired through to `aria-describedby`. Use this for
	 * destructive confirmation dialogs where the body conveys
	 * impact information that screen-reader users need announced
	 * along with the title.
	 */
	describedById?: string;
}

export function Dialog({
	open,
	onClose,
	title,
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
				'bg-card border-border m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-hidden rounded-xl border p-0 shadow-xl backdrop:bg-black/60 sm:w-full',
				'overscroll-contain',
				sizeClasses[size],
				className,
			)}
		>
			<div className="flex max-h-[calc(100dvh-2rem)] flex-col">
				<div className="border-border flex shrink-0 items-center justify-between border-b px-5 py-4">
					<h2 id={titleId} className="text-foreground text-lg font-semibold">
						{title}
					</h2>
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
