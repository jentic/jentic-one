/**
 * ApiKeyDialog — modal displaying a newly-generated API key once, and where
 * it goes: stored on the agent machine as the agent's CLI identity (the MCP
 * sheet carries the whole sequence).
 *
 * The key is cleared from parent state when the
 * dialog closes. The plaintext is never persisted beyond this component's
 * lifetime.
 */
import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Button, CopyButton, Dialog } from '@/shared/ui';
import { apiKeyWiringCommands, configName } from '@/modules/agents/lib/registerCommand';
import { useRegisterTarget } from '@/modules/agents/lib/useRegisterTarget';

interface ApiKeyDialogProps {
	open: boolean;
	apiKey: string | null;
	/** The agent the key belongs to — names the CLI identity the key is stored as. */
	agentName: string;
	onClose: () => void;
}

function maskKey(key: string): string {
	if (key.length <= 8) return '•'.repeat(key.length);
	return '•'.repeat(key.length - 8) + key.slice(-8);
}

export function ApiKeyDialog({ open, apiKey, agentName, onClose }: ApiKeyDialogProps) {
	const [revealed, setRevealed] = useState(false);
	const target = useRegisterTarget();
	// The `identity add` line of the wiring the MCP sheet shows in full; the key
	// stays a placeholder here so the command on screen never carries the secret.
	const identityCommand = apiKeyWiringCommands({ ...target, name: configName(agentName) })[1];

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
				<p className="text-muted-foreground text-sm">
					Copy this key now — it will not be shown again. If you lose it, generate a new
					one (the old key will be rotated).
				</p>
				{apiKey && (
					<div className="bg-field flex items-center gap-2 rounded-md px-3 py-2">
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
				<p className="text-muted-foreground text-sm" data-testid="api-key-where">
					On the agent machine, store it as this agent&apos;s CLI identity:
				</p>
				<code
					className="bg-field text-foreground block rounded-md px-3 py-2 font-mono text-xs break-all"
					data-testid="api-key-identity-command"
				>
					{identityCommand}
				</code>
				<p className="text-muted-foreground text-xs">
					The agent&apos;s MCP sheet has the whole sequence (environment, identity,
					context) and the <code className="font-mono">jentic mcp</code> command to run.
				</p>
			</div>
		</Dialog>
	);
}
