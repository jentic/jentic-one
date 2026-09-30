/**
 * Embedded "Shared OAuth apps" management section for the credential
 * inventory sheet. Admin-only surface. "Register shared app" opens the
 * credential create flow with "Register as a shared OAuth app" preset (the
 * flow owns API picking + endpoint seeding); this section covers the rest
 * of the lifecycle (edit, rotate secret, activate/deactivate, delete).
 */
import { useState } from 'react';
import { Plus } from 'lucide-react';
import { Button, RefreshButton, toast } from '@/shared/ui';
import { OAuthAppRegistrationsTable } from '@/shared/credentials/oauth-app-registrations/components/OAuthAppRegistrationsTable';
import { OAuthAppRegistrationEditDialog } from '@/shared/credentials/oauth-app-registrations/components/OAuthAppRegistrationEditDialog';
import { OAuthAppRegistrationRotateSecretDialog } from '@/shared/credentials/oauth-app-registrations/components/OAuthAppRegistrationRotateSecretDialog';
import { OAuthAppRegistrationDeleteDialog } from '@/shared/credentials/oauth-app-registrations/components/OAuthAppRegistrationDeleteDialog';
import {
	useOAuthAppRegistrations,
	useUpdateOAuthAppRegistration,
	type OAuthAppRegistration,
} from '@/shared/credentials/oauth-app-registrations/api/hooks';

interface SharedOAuthAppsSectionProps {
	/** Opens the host's create flow with "Register as a shared OAuth app" preset. */
	onRegister: () => void;
}

export function SharedOAuthAppsSection({ onRegister }: SharedOAuthAppsSectionProps) {
	const listQuery = useOAuthAppRegistrations({ includeInactive: true });
	const toggleActive = useUpdateOAuthAppRegistration();

	const [editTarget, setEditTarget] = useState<OAuthAppRegistration | null>(null);
	const [rotateTarget, setRotateTarget] = useState<OAuthAppRegistration | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<OAuthAppRegistration | null>(null);

	const pendingId =
		typeof toggleActive.variables === 'object' && toggleActive.variables !== null
			? (toggleActive.variables as { id: string }).id
			: null;

	const handleToggleActive = async (registration: OAuthAppRegistration): Promise<void> => {
		try {
			await toggleActive.mutateAsync({
				id: registration.id,
				input: { is_active: !registration.is_active },
			});
			toast({
				title: registration.is_active
					? 'Registration deactivated'
					: 'Registration activated',
				variant: 'success',
			});
		} catch (err) {
			toast({
				title: 'Failed to update registration',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	return (
		<section aria-labelledby="shared-oauth-apps-heading" className="space-y-3">
			<div className="flex items-center justify-between gap-2">
				<div>
					<h2
						id="shared-oauth-apps-heading"
						className="text-foreground text-base font-semibold"
					>
						Shared OAuth apps
					</h2>
					<p className="text-muted-foreground text-xs">
						OAuth apps everyone in the organization can connect through. Each person
						signs in with their own account — tokens are never shared.
					</p>
				</div>
				<div className="flex shrink-0 items-center gap-2">
					<Button size="sm" variant="secondary" onClick={onRegister}>
						<Plus className="h-4 w-4" />
						Register shared app
					</Button>
					<RefreshButton
						onRefresh={(): void => void listQuery.refetch()}
						pending={listQuery.isFetching}
						title="Refresh shared OAuth apps"
					/>
				</div>
			</div>

			<OAuthAppRegistrationsTable
				registrations={listQuery.data}
				isLoading={listQuery.isLoading}
				error={listQuery.error}
				onRegister={onRegister}
				pendingId={pendingId}
				onAction={(registration, action): void => {
					switch (action) {
						case 'edit':
							setEditTarget(registration);
							break;
						case 'rotate-secret':
							setRotateTarget(registration);
							break;
						case 'delete':
							setDeleteTarget(registration);
							break;
						case 'toggle-active':
							void handleToggleActive(registration);
							break;
					}
				}}
			/>

			<OAuthAppRegistrationEditDialog
				open={editTarget != null}
				onClose={(): void => setEditTarget(null)}
				registration={editTarget}
			/>
			<OAuthAppRegistrationRotateSecretDialog
				open={rotateTarget != null}
				onClose={(): void => setRotateTarget(null)}
				registration={rotateTarget}
			/>
			<OAuthAppRegistrationDeleteDialog
				open={deleteTarget != null}
				onClose={(): void => setDeleteTarget(null)}
				registration={deleteTarget}
			/>
		</section>
	);
}
