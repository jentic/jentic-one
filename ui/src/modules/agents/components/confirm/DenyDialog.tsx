import { useState, useEffect } from 'react';
import { Button, Dialog, Label, Textarea } from '@/shared/ui';

interface DenyDialogProps {
	open: boolean;
	subjectName: string | null;
	/** Resolves when the deny succeeds; the parent closes on success. */
	onConfirm: (reason: string) => Promise<void>;
	onClose: () => void;
	pending?: boolean;
}

/**
 * Deny confirmation with a required reason. The backend rejects an empty reason
 * with 422, so we guard it client-side too. Reset-on-commit / persist-on-dismiss:
 * the field clears only after a successful submit.
 */
export function DenyDialog({ open, subjectName, onConfirm, onClose, pending }: DenyDialogProps) {
	const [reason, setReason] = useState('');
	const [error, setError] = useState<string | null>(null);

	// Clear transient error whenever the dialog (re)opens; clear the typed reason
	// once it closes (so a kept-open dialog after a failed submit preserves the
	// draft, but a fresh open starts empty).
	useEffect(() => {
		if (open) {
			setError(null);
		} else {
			setReason('');
		}
	}, [open]);

	async function handleConfirm() {
		const trimmed = reason.trim();
		if (!trimmed) {
			setError('A reason is required.');
			return;
		}
		setError(null);
		// The parent owns success/failure (toasts on error, closes on success).
		await onConfirm(trimmed);
	}

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={subjectName ? `Deny ${subjectName}` : 'Deny'}
			size="md"
			footer={
				<>
					<Button variant="secondary" onClick={onClose} disabled={pending}>
						Cancel
					</Button>
					<Button variant="danger" onClick={handleConfirm} loading={pending}>
						Deny
					</Button>
				</>
			}
		>
			<div className="space-y-2">
				<Label htmlFor="deny-reason">Reason</Label>
				<Textarea
					id="deny-reason"
					value={reason}
					onChange={(e) => setReason(e.target.value)}
					placeholder="Explain why this registration is being denied…"
					error={error ?? undefined}
					rows={4}
					maxLength={1024}
				/>
				<p className="text-muted-foreground text-xs">
					This is recorded against the actor and shown in its detail.
				</p>
			</div>
		</Dialog>
	);
}
