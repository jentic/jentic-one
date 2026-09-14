import { ExternalLink, Loader2, ShieldAlert } from 'lucide-react';
import { Button, CopyButton, Dialog } from '@/shared/ui';
import type { DeviceAuthorizationChallengeResponse } from '@/shared/credentials/api/types';
import { isHttpsVendorUrl, openVendorUrl } from '@/shared/credentials/lib/safe-navigation';

/**
 * Renders the RFC 8628 device-code human step for a credential's
 * `POST /credentials/{id}/connect`. The `ConnectPollScanner` drives
 * completion server-side; this dialog is display-only + a Cancel affordance.
 */
export function DeviceCodeConnectDialog({
	open,
	challenge,
	credentialName,
	onCancel,
}: {
	open: boolean;
	challenge: DeviceAuthorizationChallengeResponse | null;
	credentialName: string;
	onCancel: () => void;
}) {
	if (!challenge) return null;
	const openUrl = challenge.verification_uri_complete ?? challenge.verification_uri;
	// The vendor's device-authorization JSON supplied this URL; refuse to open
	// anything that isn't https (a malicious/misconfigured vendor could return
	// ``javascript:`` or ``data:`` — see lib/safe-navigation.ts).
	const openUrlIsSafe = isHttpsVendorUrl(openUrl);
	return (
		<Dialog
			open={open}
			onClose={onCancel}
			title={`Approve ${credentialName}`}
			subtitle="Enter the code on the vendor's page. We'll pick up the approval automatically."
			size="md"
			dismissOnBackdrop={false}
			footer={
				<Button type="button" variant="ghost" size="sm" onClick={onCancel}>
					Cancel
				</Button>
			}
		>
			<div className="space-y-5">
				<div className="border-border bg-muted/30 flex flex-col items-center gap-3 rounded-xl border border-dashed p-6">
					<p className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
						Your one-time code
					</p>
					<div className="flex items-center gap-3">
						<code className="text-foreground bg-background border-border rounded-lg border px-4 py-2 font-mono text-2xl font-semibold tracking-widest">
							{challenge.user_code}
						</code>
						<CopyButton value={challenge.user_code} />
					</div>
				</div>

				{openUrlIsSafe ? (
					<Button
						type="button"
						variant="primary"
						className="w-full"
						onClick={(): void => {
							openVendorUrl(openUrl, '_blank', 'noopener,noreferrer');
						}}
					>
						<ExternalLink className="h-4 w-4" />
						Open vendor sign-in
					</Button>
				) : (
					<div className="border-destructive/40 bg-destructive/10 text-destructive flex items-start gap-2 rounded-lg border px-3 py-2.5 text-xs">
						<ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
						<p>
							The vendor returned a sign-in link that isn't a secure HTTPS URL. For
							safety we won't open it — cancel and try again.
						</p>
					</div>
				)}

				<div className="border-border bg-muted/20 flex items-center gap-2.5 rounded-lg border px-3 py-2.5">
					<Loader2 className="text-muted-foreground h-4 w-4 shrink-0 animate-spin" />
					<p className="text-muted-foreground text-xs">
						Waiting for you to approve at the vendor…
					</p>
				</div>
			</div>
		</Dialog>
	);
}
