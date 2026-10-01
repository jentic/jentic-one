/**
 * Embedded "Shared OAuth apps" management section for the credential
 * inventory sheet. Admin-only surface. "Register shared app" opens the
 * credential create flow with "Register as a shared OAuth app" preset (the
 * flow owns API picking + endpoint seeding); this section covers the rest
 * of the lifecycle (edit, rotate secret, activate/deactivate, delete).
 */
import { useId, useState } from 'react';
import { ChevronRight, Plus } from 'lucide-react';
import { Button, Dialog, RefreshButton, toast } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
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
	const bodyId = useId();
	// Collapsed by default: the section shares the inventory drawer with the
	// credential list, which is what most visits are for.
	const [expanded, setExpanded] = useState(false);

	// The edit target outlives the dialog's close so an Esc keeps the draft
	// (dialog-state-lifecycle); ``editOpen`` is what actually shows it.
	const [editTarget, setEditTarget] = useState<OAuthAppRegistration | null>(null);
	const [editOpen, setEditOpen] = useState(false);
	const [rotateTarget, setRotateTarget] = useState<OAuthAppRegistration | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<OAuthAppRegistration | null>(null);
	const [deactivateTarget, setDeactivateTarget] = useState<OAuthAppRegistration | null>(null);

	// ``variables`` outlives the mutation, so only a toggle still in flight
	// locks its row.
	const pendingId = toggleActive.isPending ? (toggleActive.variables?.id ?? null) : null;

	/** Resolves ``true`` once the change is saved; failures toast and resolve ``false``. */
	const setActive = async (
		registration: OAuthAppRegistration,
		isActive: boolean,
	): Promise<boolean> => {
		try {
			await toggleActive.mutateAsync({
				id: registration.id,
				input: { is_active: isActive },
			});
			toast({
				title: isActive
					? `${registration.name} activated`
					: `${registration.name} deactivated`,
				variant: 'success',
			});
			return true;
		} catch (err) {
			toast({
				title: 'Failed to update shared app',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
			return false;
		}
	};

	const count = listQuery.data?.length;

	return (
		<section aria-labelledby="shared-oauth-apps-heading" className="space-y-3">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="min-w-0">
					<h2
						id="shared-oauth-apps-heading"
						className="font-heading text-foreground text-base font-semibold"
					>
						<button
							type="button"
							onClick={(): void => setExpanded((v) => !v)}
							aria-expanded={expanded}
							aria-controls={bodyId}
							className="focus-visible:ring-ring flex items-center gap-1.5 rounded-sm text-left focus-visible:ring-2 focus-visible:outline-none"
						>
							<ChevronRight
								aria-hidden
								className={cn(
									'text-muted-foreground h-4 w-4 shrink-0 transition-transform',
									expanded && 'rotate-90',
								)}
							/>
							Shared OAuth apps
							{count != null && (
								<span className="text-muted-foreground text-sm font-normal tabular-nums">
									{count}
								</span>
							)}
						</button>
					</h2>
					<p className="text-muted-foreground pl-5.5 text-xs">
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

			{/* Capped so an expanded list never pushes the credential inventory
			    below it out of the sheet. */}
			<div id={bodyId} hidden={!expanded} className="max-h-[40vh] overflow-y-auto">
				{expanded && (
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
									setEditOpen(true);
									break;
								case 'rotate-secret':
									setRotateTarget(registration);
									break;
								case 'delete':
									setDeleteTarget(registration);
									break;
								case 'toggle-active':
									// Deactivating cuts off everyone at once — confirm it.
									// Re-activating is harmless, so it goes straight through.
									if (registration.is_active) setDeactivateTarget(registration);
									else void setActive(registration, true);
									break;
							}
						}}
					/>
				)}
			</div>

			<OAuthAppRegistrationEditDialog
				open={editOpen}
				onClose={(): void => setEditOpen(false)}
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
			{deactivateTarget && (
				<Dialog
					open
					onClose={(): void => setDeactivateTarget(null)}
					title={`Deactivate ${deactivateTarget.name}?`}
					size="md"
					footer={
						<>
							<Button
								variant="secondary"
								onClick={(): void => setDeactivateTarget(null)}
								disabled={toggleActive.isPending}
							>
								Cancel
							</Button>
							<Button
								variant="danger"
								loading={toggleActive.isPending}
								onClick={(): void => {
									// Stay open on failure so the admin sees it didn't happen.
									void setActive(deactivateTarget, false).then((saved) => {
										if (saved) setDeactivateTarget(null);
									});
								}}
							>
								Deactivate
							</Button>
						</>
					}
				>
					<p className="text-muted-foreground text-sm">
						Nobody in the organization can connect through this app while it&rsquo;s
						inactive
						{deactivateTarget.dependent_credential_count > 0
							? `, and the ${deactivateTarget.dependent_credential_count} credential${
									deactivateTarget.dependent_credential_count === 1 ? '' : 's'
								} created from it stop working until you reactivate it`
							: ''}
						.
					</p>
				</Dialog>
			)}
		</section>
	);
}
