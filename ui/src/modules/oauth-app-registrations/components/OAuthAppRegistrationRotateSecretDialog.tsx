/**
 * Rotate-secret modal — single input for the new client secret. Disabled
 * (and never rendered) for device-flow registrations, which have no secret;
 * the ClientsTable already hides the "Rotate secret" action for those rows,
 * but this component defends against being opened for one anyway (returns
 * null) rather than posting a request the server will 400.
 */
import { useEffect, useState } from 'react';
import { Button, Dialog, ErrorAlert, Input, Label, toast } from '@/shared/ui';
import {
	useRotateOAuthAppRegistrationSecret,
	type OAuthAppRegistration,
	type OAuthAppRegistrationFlowKind,
} from '@/modules/oauth-app-registrations/api/hooks';

interface RotateSecretDialogProps {
	open: boolean;
	onClose: () => void;
	registration: OAuthAppRegistration | null;
}

export function OAuthAppRegistrationRotateSecretDialog({
	open,
	onClose,
	registration,
}: RotateSecretDialogProps) {
	const [secret, setSecret] = useState('');
	const [validationError, setValidationError] = useState<string | null>(null);
	const mutation = useRotateOAuthAppRegistrationSecret();

	// Clear the secret input and transient error on every open — this is a
	// SENSITIVE field, so a stale value must not linger.
	useEffect(() => {
		if (!open) {
			setSecret('');
			setValidationError(null);
			mutation.reset();
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	if (!registration) return null;
	// Guard: device-authorization registrations have no client secret; the
	// caller shouldn't have opened this dialog for one, but if they did we
	// render nothing rather than trigger a 400 on submit.
	if (registration.flow_kind === ('device_authorization' as OAuthAppRegistrationFlowKind)) {
		return null;
	}

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		if (!secret.trim()) {
			setValidationError('Enter the new client secret.');
			return;
		}
		try {
			await mutation.mutateAsync({ id: registration.id, clientSecret: secret });
			setSecret('');
			toast({ title: 'Client secret rotated', variant: 'success' });
			onClose();
		} catch (err) {
			toast({
				title: 'Failed to rotate secret',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={`Rotate client secret — ${registration.name}`}
			subtitle="Existing tokens keep working; subsequent refreshes use the new secret."
			size="md"
			footer={
				<>
					<Button variant="secondary" onClick={onClose} disabled={mutation.isPending}>
						Cancel
					</Button>
					<Button type="submit" form="oar-rotate-form" loading={mutation.isPending}>
						Rotate
					</Button>
				</>
			}
		>
			<form
				id="oar-rotate-form"
				onSubmit={(e): void => void handleSubmit(e)}
				className="space-y-4"
			>
				{validationError && <ErrorAlert message={validationError} />}
				<div className="space-y-1.5">
					<Label htmlFor="oar-rotate-secret">New client secret</Label>
					<Input
						id="oar-rotate-secret"
						type="password"
						showPasswordToggle
						value={secret}
						onChange={(e): void => setSecret(e.target.value)}
						placeholder="Paste the new secret from the vendor console"
						required
					/>
					<p className="text-muted-foreground text-xs">
						The secret is stored encrypted and never re-shown after this dialog closes.
					</p>
				</div>
			</form>
		</Dialog>
	);
}
