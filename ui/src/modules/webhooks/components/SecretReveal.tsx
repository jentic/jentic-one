import { Check, KeyRound } from 'lucide-react';
import { Button, CopyButton } from '@/shared/ui';

/**
 * One-time reveal for a freshly created/rotated `whsec_…` signing secret.
 * The plaintext is returned exactly once by the API and never retrievable
 * again, so this panel makes copying obvious and requires an explicit
 * acknowledgement. Owners must wipe the value on close (sensitive-data rule).
 */
export function SecretReveal({
	secret,
	onConfirm,
	title = 'Signing secret created',
}: {
	secret: string;
	onConfirm: () => void;
	title?: string;
}) {
	return (
		<div
			className="border-success/40 bg-success/5 space-y-3 rounded-lg border p-4"
			role="alert"
		>
			<div className="flex items-center gap-2">
				<KeyRound className="text-success h-4 w-4" aria-hidden="true" />
				<p className="text-foreground text-sm font-semibold">{title}</p>
			</div>
			<p className="text-muted-foreground text-xs">
				Use this secret to verify the <code className="font-mono">webhook-signature</code>{' '}
				header on every delivery. Copy it now — it is shown only once and cannot be
				retrieved again.
			</p>
			<div className="bg-card border-border flex items-center gap-2 rounded-md border p-2">
				<code className="text-foreground min-w-0 flex-1 truncate font-mono text-xs">
					{secret}
				</code>
				<CopyButton value={secret} size="sm" />
			</div>
			<Button size="sm" onClick={onConfirm}>
				<Check className="h-4 w-4" /> I&rsquo;ve saved it
			</Button>
		</div>
	);
}
