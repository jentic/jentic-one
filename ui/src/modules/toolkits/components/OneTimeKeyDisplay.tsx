import { Check, KeyRound } from 'lucide-react';
import { Button, CopyButton } from '@/shared/ui';

/**
 * One-time secret reveal for a freshly created toolkit API key. The plaintext
 * `jntc_live_…` value is returned ONCE by `POST /toolkits/{id}/keys` and is
 * never retrievable again, so this panel makes copying obvious and requires an
 * explicit "I've saved it" acknowledgement before it can be dismissed.
 */
export interface OneTimeKeyDisplayProps {
	keyValue: string;
	onConfirm: () => void;
	title?: string;
}

export function OneTimeKeyDisplay({
	keyValue,
	onConfirm,
	title = 'New API Key Created',
}: OneTimeKeyDisplayProps) {
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
				Copy this key now — it is shown only once and cannot be retrieved again.
			</p>
			<div className="bg-card border-border flex items-center gap-2 rounded-md border p-2">
				<code className="text-foreground min-w-0 flex-1 truncate font-mono text-xs">
					{keyValue}
				</code>
				<CopyButton value={keyValue} size="sm" />
			</div>
			<Button size="sm" onClick={onConfirm}>
				<Check className="h-4 w-4" /> I&rsquo;ve saved it
			</Button>
		</div>
	);
}
