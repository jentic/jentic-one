/**
 * Page-level confirm/secret dialogs for the OAuth-clients section (the
 * agents-roster grammar: row menus fire, the SECTION owns the dialogs).
 * All three are stateless (no drafts), so conditional mounting by the owner
 * is fine per the dialog-state rule.
 */
import { Button, CopyButton, Dialog, ErrorAlert } from '@/shared/ui';

/**
 * One-time secret reveal (create / rotate). Stays a `Dialog` — a blocking
 * decision moment — and the OWNER wipes the secret on close (sensitive-data
 * exception: the secret must not survive a dismissal).
 */
export function SecretDialog({
	open,
	onClose,
	secret,
	title,
}: {
	open: boolean;
	onClose: () => void;
	secret: string;
	title: string;
}) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={title}
			dismissOnBackdrop={false}
			footer={<Button onClick={onClose}>Done</Button>}
		>
			<div className="space-y-3">
				<p className="text-muted-foreground text-xs">
					Copy this secret now — it is shown only once and cannot be retrieved again.
				</p>
				<div className="bg-card border-border flex items-center gap-2 rounded-md border p-2">
					<code className="text-foreground min-w-0 flex-1 overflow-x-auto font-mono text-xs">
						{secret}
					</code>
					<CopyButton value={secret} variant="ghost" size="icon" toastMessage={false} />
				</div>
			</div>
		</Dialog>
	);
}

export function RotateConfirmDialog({
	open,
	onClose,
	onConfirm,
	isPending,
	clientName,
}: {
	open: boolean;
	onClose: () => void;
	onConfirm: () => void;
	isPending: boolean;
	clientName: string;
}) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Rotate Client Secret?"
			footer={
				<>
					<Button variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button variant="danger" onClick={onConfirm} disabled={isPending}>
						{isPending ? 'Rotating...' : 'Rotate Secret'}
					</Button>
				</>
			}
		>
			<p className="text-muted-foreground">
				This will invalidate the current secret for <strong>{clientName}</strong>{' '}
				immediately. Any application using the old secret will lose access.
			</p>
		</Dialog>
	);
}

/**
 * The reversible kill-switch confirm. The wire verb stays `deactivate`
 * (`DELETE /admin/oauth-clients/{id}`); user-facing copy says **Disable** —
 * the lifecycle vocabulary's reversible pair (Disable/Enable), distinct from
 * the permanent Delete in the danger zone's type-to-confirm dialog.
 */
export function DisableConfirmDialog({
	open,
	onClose,
	onConfirm,
	isPending,
	clientName,
	error,
}: {
	open: boolean;
	onClose: () => void;
	onConfirm: () => void;
	isPending: boolean;
	clientName: string;
	error?: unknown;
}) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Disable OAuth Client?"
			footer={
				<>
					<Button variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button variant="danger" onClick={onConfirm} disabled={isPending}>
						{isPending ? 'Disabling...' : 'Disable'}
					</Button>
				</>
			}
		>
			<p className="text-muted-foreground">
				This will prevent <strong>{clientName}</strong> from initiating new authorization
				flows. Existing sessions are not affected. You can enable the client again later —
				and a disabled client that re-registers via DCR returns to the approval queue.
			</p>
			{error != null && (
				<div className="mt-3">
					<ErrorAlert
						message={error instanceof Error ? error : 'Failed to disable the client'}
					/>
				</div>
			)}
		</Dialog>
	);
}
