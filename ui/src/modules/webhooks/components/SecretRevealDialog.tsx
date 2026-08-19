/**
 * SecretRevealDialog — shows a signing secret the one and only time it exists in
 * plaintext.
 *
 * This component is the reason the create/rotate mutations return their secret
 * to the caller instead of caching it. The backend stores only an encrypted
 * copy, so once this dialog is dismissed the value is unrecoverable and the
 * operator's sole remedy is another rotation.
 *
 * Consequences, all deliberate:
 *
 * - **Not dismissible by backdrop click.** A stray click outside a modal is the
 *   single easiest way to lose the value forever. Escape and the explicit
 *   "Done" button still close it.
 * - **Requires an explicit acknowledgement** before Done enables. The checkbox
 *   is friction on purpose: it converts "I closed a dialog" into "I stored the
 *   secret".
 * - **Conditionally mounted by the parent** so the plaintext leaves React state
 *   entirely on close, rather than lingering in a mounted-but-hidden component.
 *   This is the documented sensitive-data exception to the project's
 *   "persist drafts between dismissals" dialog rule — there is no draft here,
 *   and persistence is precisely what we don't want.
 */
import { useEffect, useState } from 'react';
import { AlertTriangle, BookOpen } from 'lucide-react';
import { Button, Checkbox, CopyButton, Dialog } from '@/shared/ui';

interface SecretRevealDialogProps {
	open: boolean;
	onClose: () => void;
	secret: string;
	/** Endpoint name, for context in the title. */
	endpointName: string;
	/** Distinguishes first issue from a re-issue in the copy. */
	mode: 'created' | 'rotated';
	/**
	 * For a rotation: when the previous secret stops working. `null` means it was
	 * revoked immediately, which is worth saying out loud — anything still
	 * signing with the old key is already failing.
	 */
	previousSecretExpiresAt?: string | null;
	/**
	 * Opens the relay guide. Offered only on first creation, where the user's
	 * very next task is to stand up a relay that uses the secret they just saved.
	 */
	onOpenRelayGuide?: () => void;
}

export function SecretRevealDialog({
	open,
	onClose,
	secret,
	endpointName,
	mode,
	previousSecretExpiresAt,
	onOpenRelayGuide,
}: SecretRevealDialogProps) {
	const [acknowledged, setAcknowledged] = useState(false);

	// Transient flag, not user input — clear on every (re)open so a previous
	// acknowledgement can't pre-satisfy the gate for a different secret.
	useEffect(() => {
		if (open) setAcknowledged(false);
	}, [open]);

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={mode === 'created' ? 'Save your signing secret' : 'Your new signing secret'}
			subtitle={endpointName}
			dismissOnBackdrop={false}
			size="md"
			footer={
				<Button variant="primary" onClick={onClose} disabled={!acknowledged}>
					Done
				</Button>
			}
		>
			<div className="space-y-4">
				<div className="border-warning/30 bg-warning/10 flex gap-3 rounded-lg border p-3">
					<AlertTriangle className="text-warning mt-0.5 h-4 w-4 shrink-0" />
					<p className="text-foreground text-sm leading-relaxed">
						This is the only time this secret is shown. Jentic One keeps an encrypted
						copy it cannot reverse for display, so if you lose it the only way forward
						is to rotate again.
					</p>
				</div>

				<div className="space-y-1.5">
					<span className="text-muted-foreground text-xs tracking-wider uppercase">
						Signing secret
					</span>
					<div className="border-border bg-muted flex items-center gap-2 rounded-lg border p-3">
						<code className="text-foreground min-w-0 flex-1 font-mono text-xs break-all">
							{secret}
						</code>
						<CopyButton value={secret} ariaLabel="Copy signing secret" />
					</div>
				</div>

				{mode === 'rotated' && (
					<p className="text-muted-foreground text-sm leading-relaxed">
						{previousSecretExpiresAt
							? `The previous secret keeps working until ${new Date(previousSecretExpiresAt).toLocaleString()}, so you can update both sides without dropping events.`
							: 'The previous secret was revoked immediately. Anything still signing with it will be rejected from now on.'}
					</p>
				)}

				<Checkbox checked={acknowledged} onChange={setAcknowledged}>
					I&apos;ve stored this secret somewhere safe
				</Checkbox>

				{mode === 'created' && onOpenRelayGuide && (
					<div className="border-border bg-muted/40 flex items-center justify-between gap-3 rounded-lg border p-3">
						<p className="text-muted-foreground text-xs leading-relaxed">
							Next: stand up a relay that verifies this secret and forwards events to
							your destination.
						</p>
						<Button
							type="button"
							variant="secondary"
							size="sm"
							onClick={onOpenRelayGuide}
							className="shrink-0"
						>
							<BookOpen className="h-4 w-4" />
							Relay guide
						</Button>
					</div>
				)}
			</div>
		</Dialog>
	);
}
