/**
 * ApiKeyDialog — modal displaying a newly-generated API key once.
 *
 * The key is cleared from parent state when the
 * dialog closes. The plaintext is never persisted beyond this component's
 * lifetime.
 */
import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Button, CopyButton, Dialog } from '@/shared/ui';

interface ApiKeyDialogProps {
	open: boolean;
	apiKey: string | null;
	onClose: () => void;
}

function maskKey(key: string): string {
	if (key.length <= 8) return '•'.repeat(key.length);
	return '•'.repeat(key.length - 8) + key.slice(-8);
}

export function ApiKeyDialog({ open, apiKey, onClose }: ApiKeyDialogProps) {
	const [revealed, setRevealed] = useState(false);

	const handleClose = () => {
		setRevealed(false);
		onClose();
	};

	return (
		<Dialog
			open={open}
			onClose={handleClose}
			title="API key generated"
			size="sm"
			footer={
				<Button variant="primary" onClick={handleClose}>
					Done
				</Button>
			}
		>
			<div className="space-y-3">
				<div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
					<strong>Note:</strong> Manual API key generation is not the recommended pattern
					for agent authentication. Prefer{' '}
					<span className="font-medium">agent OAuth self-registration</span> for
					production workloads.
				</div>
				<p className="text-muted-foreground text-sm">
					Copy this key now — it will not be shown again. If you lose it, generate a new
					one (the old key will be rotated).
				</p>
				{apiKey && (
					<div className="bg-muted/50 border-border flex items-center gap-2 rounded-lg border px-3 py-2">
						<code className="text-foreground min-w-0 flex-1 truncate font-mono text-xs break-all">
							{revealed ? apiKey : maskKey(apiKey)}
						</code>
						<button
							type="button"
							onClick={() => setRevealed(!revealed)}
							className="text-muted-foreground hover:text-foreground shrink-0"
							aria-label={revealed ? 'Hide API key' : 'Reveal API key'}
						>
							{revealed ? (
								<EyeOff className="h-4 w-4" />
							) : (
								<Eye className="h-4 w-4" />
							)}
						</button>
						<CopyButton value={apiKey} />
					</div>
				)}
			</div>
		</Dialog>
	);
}
