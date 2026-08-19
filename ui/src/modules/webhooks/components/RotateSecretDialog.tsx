/**
 * RotateSecretDialog — re-issues an endpoint's signing secret.
 *
 * The only interesting choice here is the **grace period**: how long the previous
 * secret keeps working. It exists because rotation is otherwise a coordination
 * problem — the sender and receiver cannot swap keys at the same instant, and
 * without an overlap every event in that gap fails signature verification.
 *
 * So the two options are genuinely different operations:
 *
 * - **Keep the old secret working (default 24h)** — a planned rotation. Update the
 *   other side at leisure; nothing drops.
 * - **Revoke immediately** — a leak. The old secret must stop working *now*, and
 *   losing in-flight events is the accepted cost of containment.
 *
 * Defaulting to the graceful path keeps a routine rotation from causing an
 * outage, while leaving the destructive option one click away for when it's the
 * right answer.
 */
import { useEffect, useState } from 'react';
import { Button, Checkbox, Dialog } from '@/shared/ui';
import { useRotateWebhookSecret } from '@/modules/webhooks/api';
import type { RotatedSecret, WebhookEndpointEntity } from '@/modules/webhooks/api';

interface RotateSecretDialogProps {
	open: boolean;
	onClose: () => void;
	endpoint: WebhookEndpointEntity | null;
	/** Hands the new secret up so it can be revealed once. */
	onRotated: (rotated: RotatedSecret, endpoint: WebhookEndpointEntity) => void;
}

export function RotateSecretDialog({
	open,
	onClose,
	endpoint,
	onRotated,
}: RotateSecretDialogProps) {
	const [revokeNow, setRevokeNow] = useState(false);
	const rotate = useRotateWebhookSecret();

	// A destructive non-default must never persist across opens: the next
	// rotation is far more likely to be routine than a leak.
	useEffect(() => {
		if (open) setRevokeNow(false);
	}, [open]);

	async function handleRotate() {
		if (!endpoint) return;
		try {
			const rotated = await rotate.mutateAsync({
				endpointId: endpoint.id,
				// `undefined` takes the backend's 24h default; 0 revokes at once.
				graceSeconds: revokeNow ? 0 : undefined,
			});
			onClose();
			onRotated(rotated, endpoint);
		} catch {
			// The hook surfaces a toast; leave the dialog open to retry.
		}
	}

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Rotate signing secret"
			subtitle={endpoint?.name}
			size="md"
			footer={
				<>
					<Button variant="secondary" onClick={onClose}>
						Cancel
					</Button>
					<Button variant="primary" onClick={handleRotate} loading={rotate.isPending}>
						Rotate secret
					</Button>
				</>
			}
		>
			<div className="space-y-4">
				<p className="text-foreground text-sm leading-relaxed">
					A new secret is issued and shown once. By default the previous secret keeps
					working for 24 hours, so you can update the other side without dropping events.
				</p>
				<Checkbox checked={revokeNow} onChange={setRevokeNow}>
					Revoke the previous secret immediately
				</Checkbox>
				{revokeNow && (
					<p className="border-warning/30 bg-warning/10 text-foreground rounded-lg border p-3 text-sm leading-relaxed">
						Anything still signing with the old secret starts failing at once. Correct
						if the secret leaked — otherwise prefer the grace period.
					</p>
				)}
			</div>
		</Dialog>
	);
}
