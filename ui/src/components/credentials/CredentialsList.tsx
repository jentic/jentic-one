import { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Key } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CredentialRow, CredentialsListSkeleton } from './CredentialRow';
import { PipedreamCard } from './PipedreamCard';
import { OAuthConnectionDetailSheet } from './OAuthConnectionDetailSheet';
import type { CredentialOut } from '@/api/types';
import { api, oauthBrokers } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { ConfirmDeleteDialog, type DeleteTarget } from '@/components/ui/ConfirmDeleteDialog';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { toast } from '@/components/ui/toastStore';
import { SectionTitle } from '@/components/discovery/SectionTitle';
import { useOAuthConnectionSheet } from '@/hooks/useOAuthConnectionSheet';

// Mirrors the Toolkits list entrance: a staggered grid (0.04s between cards)
// where each card fades + lifts in. Kept in sync with `toolkitCardVariants`
// / `gridVariants` so Credentials and Toolkits read as one family.
const gridVariants = {
	hidden: { opacity: 1 },
	visible: { opacity: 1, transition: { staggerChildren: 0.04 } },
} as const;

const cardVariants = {
	hidden: { opacity: 0, y: 8 },
	visible: { opacity: 1, y: 0, transition: { duration: 0.2, ease: 'easeOut' } },
} as const;

interface CredentialsListProps {
	loggedIn: boolean;
	/**
	 * Optional handler invoked when the user clicks Edit on a manual
	 * (non-Pipedream) credential row. Hosts that mount a
	 * `CredentialEditSheet` pass this and forward the click to the
	 * sheet's open handler. When omitted the row falls back to the
	 * legacy `/credentials/:id/edit` navigation, which still works.
	 */
	onEditCredential?: (cred: CredentialOut) => void;
	/**
	 * Optional handler for the "Add your first credential" CTA in the
	 * empty state. Hosts that mount an `<AddCredentialDialog>` wire
	 * this to the dialog's `openWorkspace()` action. Falls back to a
	 * `/credentials/new` navigation when omitted, preserving the
	 * standalone deeplink behaviour.
	 */
	onAddCredential?: () => void;
}

/**
 * The credentials list — Pipedream status line on top, then a section
 * per kind (manual creds + Pipedream-managed) with rows from
 * `<CredentialRow>`. The page shell is the parent's job; this component
 * is purely the body.
 *
 * Why split kinds into sections?
 * Pipedream creds and manually-uploaded creds are conceptually different:
 * - manual creds are owned by the user (rotate locally),
 * - Pipedream creds are owned by an OAuth grant (rotate by reconnect).
 *
 * Mixing them as one flat list led to repeated user confusion ("why is
 * there no value field on this one?"). The section split lets the row UI
 * stay simple — each row only renders the controls that make sense for its
 * kind, no `if (auth_type === 'pipedream_oauth')` branching at the row
 * level.
 *
 * Clicking a row opens a kind-appropriate surface: manual creds open the
 * host's `CredentialEditSheet` (via `onEditCredential`); OAuth connections
 * open this component's own `OAuthConnectionDetailSheet` (read-mostly
 * facts + sync-safe metadata edits + reconnect/delete), since their secret
 * lives upstream and there's nothing to rotate locally.
 */
export function CredentialsList({
	loggedIn,
	onEditCredential,
	onAddCredential,
}: CredentialsListProps) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const oauthSheet = useOAuthConnectionSheet();

	const {
		data: credentials = [],
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['credentials'],
		queryFn: () => api.listCredentials(),
		select: (d: unknown) => {
			if (Array.isArray(d)) return d as CredentialOut[];
			const data = (d as { data?: unknown })?.data;
			return Array.isArray(data) ? (data as CredentialOut[]) : [];
		},
		enabled: loggedIn,
	});

	const [manualCreds, pipedreamCreds] = useMemo(() => {
		const manual: CredentialOut[] = [];
		const pipedream: CredentialOut[] = [];
		credentials.forEach((c) => {
			if (c.auth_type === 'pipedream_oauth') pipedream.push(c);
			else manual.push(c);
		});
		return [manual, pipedream];
	}, [credentials]);

	const deleteMutation = useMutation({
		mutationFn: (cred: { id: string; authType: string; accountId?: string }) => {
			if (cred.authType === 'pipedream_oauth' && cred.accountId) {
				return oauthBrokers.deleteAccount('pipedream', cred.accountId);
			}
			return api.deleteCredential(cred.id);
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			// Deleting a Pipedream connection removes its oauth_broker_accounts
			// row; the broker-accounts query backs the connection cards, so it
			// must refresh too or the deleted connection lingers.
			queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
			queryClient.invalidateQueries({ queryKey: ['toolkit-card-enrichment'] });
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			setDeleteTarget(null);
			toast({ title: 'Credential deleted', variant: 'success' });
		},
		onError: (err: any) => {
			setDeleteTarget(null);
			toast({
				title: 'Failed to delete credential',
				description:
					err?.body?.error ?? err?.message ?? 'The server rejected the deletion.',
				variant: 'error',
			});
		},
	});

	// Lifted dialog state — `ConfirmInline` (the previous prompt) was a
	// 50/50 click target without enough room to show toolkit bindings, so
	// users learned to delete creds and *then* discover (in Slack, after
	// the fact) that two toolkits stopped working. The dialog reads the
	// bindings from `/credentials/{id}/bindings` before the user commits.
	const [deleteTarget, setDeleteTarget] = useState<{
		id: string;
		authType: string;
		accountId?: string;
		name: string;
		isPipedream: boolean;
	} | null>(null);

	const dialogTarget: DeleteTarget | null = deleteTarget
		? {
				kind: 'credential',
				id: deleteTarget.id,
				name: deleteTarget.name,
				isPipedream: deleteTarget.isPipedream,
			}
		: null;

	if (!loggedIn || isLoading) {
		return <CredentialsListSkeleton />;
	}
	if (isError) {
		return <ErrorAlert message="Failed to load credentials. Please try refreshing the page." />;
	}
	if (credentials.length === 0) {
		return (
			<>
				<PipedreamCard />
				<EmptyState
					icon={<Key className="h-10 w-10 opacity-30" />}
					title="No credentials stored"
					description="Add a credential to authenticate agents with external APIs."
					action={
						<Button
							onClick={() =>
								onAddCredential ? onAddCredential() : navigate('/credentials/new')
							}
						>
							Add your first credential
						</Button>
					}
				/>
			</>
		);
	}

	return (
		<div className="space-y-5">
			<PipedreamCard />

			{manualCreds.length > 0 && (
				<section>
					<SectionTitle count={manualCreds.length}>Credentials</SectionTitle>
					<motion.div
						variants={gridVariants}
						initial="hidden"
						animate="visible"
						className="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
					>
						{manualCreds.map((cred) => (
							<motion.div key={cred.id} variants={cardVariants}>
								<CredentialRow
									cred={cred}
									onEdit={onEditCredential}
									onDelete={() =>
										setDeleteTarget({
											id: cred.id,
											authType: cred.auth_type ?? '',
											name: cred.label,
											isPipedream: false,
										})
									}
								/>
							</motion.div>
						))}
					</motion.div>
				</section>
			)}

			{pipedreamCreds.length > 0 && (
				<section>
					<SectionTitle count={pipedreamCreds.length}>OAuth connections</SectionTitle>
					<motion.div
						variants={gridVariants}
						initial="hidden"
						animate="visible"
						className="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
					>
						{pipedreamCreds.map((cred) => (
							<motion.div key={cred.id} variants={cardVariants}>
								<CredentialRow
									cred={cred}
									onEditPipedream={() => oauthSheet.openSheet(cred.id)}
									onReconnect={() => oauthSheet.openSheet(cred.id)}
									onDelete={() =>
										setDeleteTarget({
											id: cred.id,
											authType: 'pipedream_oauth',
											accountId: cred.account_id ?? undefined,
											name: cred.label,
											isPipedream: true,
										})
									}
								/>
							</motion.div>
						))}
					</motion.div>
				</section>
			)}

			<ConfirmDeleteDialog
				target={dialogTarget}
				open={dialogTarget != null}
				onClose={() => {
					if (!deleteMutation.isPending) setDeleteTarget(null);
				}}
				onConfirm={() => {
					if (!deleteTarget) return;
					deleteMutation.mutate({
						id: deleteTarget.id,
						authType: deleteTarget.authType,
						accountId: deleteTarget.accountId,
					});
				}}
				loading={deleteMutation.isPending}
			/>

			<OAuthConnectionDetailSheet
				credentialId={oauthSheet.stickyId}
				open={oauthSheet.open}
				onClose={oauthSheet.closeSheet}
				onAfterClose={oauthSheet.clearSticky}
				onDelete={(cred) => {
					// Hand the destructive path to the shared, cascade-aware
					// confirm dialog (same one the row's trash button uses) so
					// the user sees which toolkits would lose this connection.
					oauthSheet.closeSheet();
					setDeleteTarget({
						id: cred.id,
						authType: 'pipedream_oauth',
						accountId: cred.account_id ?? undefined,
						name: cred.label,
						isPipedream: true,
					});
				}}
			/>
		</div>
	);
}
