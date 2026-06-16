import { Check, KeyRound, Link as LinkIcon } from 'lucide-react';
import type { ApiOut } from '@/api/types';
import { Button } from '@/components/ui/Button';

/**
 * Step 4 of `<AddCredentialDialog>` — a "you're done" panel that
 * mirrors what the toolkit binding flow in the webapp surfaces after
 * a successful add. Two purposes:
 *
 *   1. Tell the user explicitly what happened (workspace credential
 *      created, optionally bound to a toolkit).
 *   2. Give them a single, obvious "Done" affordance that closes the
 *      dialog. Hosts that want a different terminal action (e.g.
 *      "Open the new credential's edit sheet") can intercept Save in
 *      Configure and skip Confirm; the dialog supports that path.
 */
export interface ConfirmStepProps {
	selectedApi: ApiOut;
	mode: 'workspace' | 'toolkit';
	toolkitName: string | null;
	credentialLabel: string | null;
	onDone: () => void;
}

export function ConfirmStep({
	selectedApi,
	mode,
	toolkitName,
	credentialLabel,
	onDone,
}: ConfirmStepProps) {
	return (
		<div className="space-y-5 py-2">
			<div className="bg-success/10 border-success/30 flex items-start gap-3 rounded-xl border px-4 py-3">
				<div className="bg-success/20 text-success flex h-8 w-8 shrink-0 items-center justify-center rounded-full">
					<Check className="h-4 w-4" />
				</div>
				<div className="min-w-0 flex-1">
					<p className="text-foreground text-sm font-semibold">
						Credential saved{mode === 'toolkit' ? ' and bound' : ''}
					</p>
					<p className="text-muted-foreground mt-0.5 text-xs">
						You can edit, test, or rotate it any time from the Credentials page.
					</p>
				</div>
			</div>

			<dl className="border-border space-y-3 rounded-xl border p-4">
				<div className="flex items-start gap-3">
					<KeyRound className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" />
					<div className="min-w-0 flex-1">
						<dt className="text-muted-foreground text-xs">Credential</dt>
						<dd className="text-foreground mt-0.5 text-sm font-medium">
							{credentialLabel || selectedApi.name || selectedApi.id}
						</dd>
						<p className="text-muted-foreground truncate font-mono text-[11px]">
							{selectedApi.id}
						</p>
					</div>
				</div>

				{mode === 'toolkit' && toolkitName && (
					<div className="border-border/50 flex items-start gap-3 border-t pt-3">
						<LinkIcon className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" />
						<div className="min-w-0 flex-1">
							<dt className="text-muted-foreground text-xs">Bound to toolkit</dt>
							<dd className="text-foreground mt-0.5 text-sm font-medium">
								{toolkitName}
							</dd>
						</div>
					</div>
				)}
			</dl>

			<Button onClick={onDone} fullWidth>
				Done
			</Button>
		</div>
	);
}
