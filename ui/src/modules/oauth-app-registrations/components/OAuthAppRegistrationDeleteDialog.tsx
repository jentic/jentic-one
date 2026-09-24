/**
 * Delete confirm — a lightweight custom dialog rather than
 * `CascadeDeleteDialog` because the OAuth-app-registration entity type isn't
 * listed there (yet). We surface the same "cannot delete while credentials
 * still reference it" branch when the API returns a 409 (slug
 * `oauth_app_registration_in_use`), so the admin's next step is obvious:
 * revoke or deactivate the dependent credentials first.
 */
import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button, Dialog, ErrorAlert, toast } from '@/shared/ui';
import { isInUseConflict } from '@/modules/oauth-app-registrations/api/client';
import {
	useDeleteOAuthAppRegistration,
	type OAuthAppRegistration,
} from '@/modules/oauth-app-registrations/api/hooks';

interface DeleteDialogProps {
	open: boolean;
	onClose: () => void;
	registration: OAuthAppRegistration | null;
}

export function OAuthAppRegistrationDeleteDialog({
	open,
	onClose,
	registration,
}: DeleteDialogProps) {
	const mutation = useDeleteOAuthAppRegistration();
	const [inUseError, setInUseError] = useState<string | null>(null);
	const [genericError, setGenericError] = useState<string | null>(null);

	useEffect(() => {
		if (!open) {
			setInUseError(null);
			setGenericError(null);
			mutation.reset();
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	if (!registration) return null;

	const handleConfirm = async (): Promise<void> => {
		try {
			await mutation.mutateAsync(registration.id);
			toast({ title: `${registration.name} deleted`, variant: 'success' });
			onClose();
		} catch (err) {
			if (isInUseConflict(err)) {
				setInUseError(
					`This registration is still referenced by ${registration.dependent_credential_count} credential${
						registration.dependent_credential_count === 1 ? '' : 's'
					}. Revoke or deactivate those first, or PATCH is_active=false instead of deleting.`,
				);
				return;
			}
			setGenericError(err instanceof Error ? err.message : String(err));
		}
	};

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={`Delete ${registration.name}?`}
			size="md"
			footer={
				<>
					<Button variant="secondary" onClick={onClose} disabled={mutation.isPending}>
						Cancel
					</Button>
					<Button
						variant="danger"
						onClick={(): void => void handleConfirm()}
						loading={mutation.isPending}
					>
						Delete
					</Button>
				</>
			}
		>
			<div className="space-y-3">
				<div className="text-danger bg-danger/10 border-danger/30 flex items-start gap-2 rounded-md border p-3 text-sm">
					<AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<p>
						Deleting removes this shared OAuth application registration. Users who SSO
						through it can no longer do so.
					</p>
				</div>
				{inUseError && <ErrorAlert message={inUseError} />}
				{genericError && <ErrorAlert message={genericError} />}
			</div>
		</Dialog>
	);
}
