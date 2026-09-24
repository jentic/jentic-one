/**
 * OAuth app registrations admin surface: the management view for shared
 * OAuth applications that end users SSO through. Admin-only (gated in
 * routes.tsx via `RequirePermission`).
 *
 * Registration *creation* lives on the normal credentials page — an admin
 * flips the "Available to everyone in the organization" toggle on the
 * OAuth2 create form there. This page covers the rest of the lifecycle:
 * edit endpoints / labels, rotate the client secret, deactivate, delete.
 */
import { useState } from 'react';
import { Link } from 'react-router';
import { ArrowUpRight } from 'lucide-react';
import { Button, PageHeader, PageHelp, PageShell, RefreshButton, toast } from '@/shared/ui';
import { ROUTES } from '@/shared/app';
import {
	useOAuthAppRegistrations,
	useUpdateOAuthAppRegistration,
	type OAuthAppRegistration,
} from '@/modules/oauth-app-registrations/api/hooks';
import { OAuthAppRegistrationsTable } from '@/modules/oauth-app-registrations/components/OAuthAppRegistrationsTable';
import { OAuthAppRegistrationEditDialog } from '@/modules/oauth-app-registrations/components/OAuthAppRegistrationEditDialog';
import { OAuthAppRegistrationRotateSecretDialog } from '@/modules/oauth-app-registrations/components/OAuthAppRegistrationRotateSecretDialog';
import { OAuthAppRegistrationDeleteDialog } from '@/modules/oauth-app-registrations/components/OAuthAppRegistrationDeleteDialog';

export function OAuthAppRegistrationsPage() {
	// Resolved at render time (not module init) — the shared route table
	// registers this module's routes eagerly, so touching ROUTES during
	// module import creates a circular-init hazard.
	const credentialsHref = ROUTES.credentials;
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
		<PageShell>
			<PageHeader
				title="OAuth App Registrations"
				subtitle="Shared OAuth applications your users can SSO through. Create new registrations from the credentials page; manage rotation and lifecycle here."
				actions={
					<div className="flex items-center gap-2">
						<RefreshButton
							onRefresh={(): void => void listQuery.refetch()}
							pending={listQuery.isFetching}
							title="Refresh registrations"
						/>
						<Link to={credentialsHref}>
							<Button variant="secondary">
								Register from credentials
								<ArrowUpRight className="h-4 w-4" />
							</Button>
						</Link>
						<PageHelp
							title="About OAuth app registrations"
							intro="Each registration lets users on this instance sign in to a specific external API through a shared OAuth application."
							sections={[
								{
									heading: 'Adding a new registration',
									body: 'Open the credentials page and start creating an OAuth2 credential. Admins see an “Available to everyone in the organization” toggle — flip it to submit as an org-shared registration instead of a personal credential.',
								},
								{
									heading: 'Authorization code flow',
									body: 'Standard OAuth 2.0 flow requiring a client_id + client_secret + redirect URI. Rotate the secret with the refresh action.',
								},
								{
									heading: 'Device authorization flow',
									body: 'RFC 8628 device flow — no client secret, no redirect URI. Ideal for CLIs and headless clients.',
								},
								{
									heading: 'Deleting a registration',
									body: 'A delete is refused while any credentials still reference the registration — deactivate it, or revoke those credentials first.',
								},
							]}
						/>
					</div>
				}
			/>

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
		</PageShell>
	);
}
