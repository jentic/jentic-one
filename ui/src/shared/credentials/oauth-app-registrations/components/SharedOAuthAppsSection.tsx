/**
 * Embedded "Shared OAuth apps" management section for the credentials
 * page. Admin-only surface — creation still lives on the credentials Add
 * dialog with the "Available to everyone in the organization" toggle;
 * this section covers the rest of the lifecycle (edit, rotate secret,
 * activate/deactivate, delete).
 */
import { useState } from 'react';
import { RefreshButton, toast } from '@/shared/ui';
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
	/**
	 * Where the empty state should point admins to register a new shared
	 * app. Registration happens from the credentials Add dialog with the
	 * "Available to everyone in the organization" toggle flipped.
	 */
	credentialsHref: string;
}

export function SharedOAuthAppsSection({ credentialsHref }: SharedOAuthAppsSectionProps) {
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
						OAuth applications any user on this instance can SSO through. Add one by
						flipping "Available to everyone in the organization" on the credentials Add
						dialog.
					</p>
				</div>
				<RefreshButton
					onRefresh={(): void => void listQuery.refetch()}
					pending={listQuery.isFetching}
					title="Refresh shared OAuth apps"
				/>
			</div>

			<OAuthAppRegistrationsTable
				registrations={listQuery.data}
				isLoading={listQuery.isLoading}
				error={listQuery.error}
				credentialsHref={credentialsHref}
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
