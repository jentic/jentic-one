import { useMemo, useState } from 'react';
import { Plus } from 'lucide-react';
import { Button, CascadeDeleteDialog, PageHeader, PageHelp, PageShell, toast } from '@/shared/ui';
import {
	CredentialType,
	runConnectFlow,
	useCredentials,
	useDeleteCredential,
	type Credential,
} from '@/modules/credentials/api';
import { CredentialsList } from '@/modules/credentials/components/CredentialsList';
import {
	CredentialsToolbar,
	type CredentialTypeFilter,
} from '@/modules/credentials/components/CredentialsToolbar';
import {
	CreateCredentialDialog,
	type CreatedCredentialInfo,
} from '@/modules/credentials/components/CreateCredentialDialog';
import { EditCredentialSheet } from '@/modules/credentials/components/EditCredentialSheet';

/**
 * Credentials module home. Lists stored credentials and hosts the create
 * dialog, edit sheet, and the delete confirmation. Data flows through the
 * module's React Query hooks only.
 *
 * Note: we used to surface a one-time secret dialog after creation to echo
 * back the raw secret. That added friction without security benefit for
 * user-provided values, so it was removed — the success toast is now the
 * sole feedback for a created credential.
 */
export function CredentialsPage() {
	const [search, setSearch] = useState('');
	const [typeFilter, setTypeFilter] = useState<CredentialTypeFilter>('all');
	const [createOpen, setCreateOpen] = useState(false);
	const [editId, setEditId] = useState<string | null>(null);
	const [stickyEditId, setStickyEditId] = useState<string | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<Credential | null>(null);

	const { data, isLoading, error, refetch, isFetching } = useCredentials();
	const deleteMutation = useDeleteCredential();

	const credentials = useMemo(() => data?.data ?? [], [data]);

	const filtered = useMemo(() => {
		const q = search.trim().toLowerCase();
		return credentials.filter((c) => {
			if (typeFilter !== 'all' && c.type !== typeFilter) return false;
			if (!q) return true;
			return (
				c.name.toLowerCase().includes(q) ||
				c.api.vendor?.toLowerCase().includes(q) ||
				c.provider?.toLowerCase().includes(q)
			);
		});
	}, [credentials, search, typeFilter]);

	const openEdit = (cred: Credential): void => {
		setStickyEditId(cred.credential_id);
		setEditId(cred.credential_id);
	};

	/**
	 * Connect a credential immediately after it was created. Unlike the
	 * standalone Connect action, an OAuth2 credential that never completes its
	 * sign-in here is freshly-created and unusable — "if it wasn't signed, we
	 * shouldn't store it". So when the handshake is abandoned (the user closed
	 * the popup), times out, or errors, we delete the dangling credential
	 * instead of leaving an unconnected row in the list.
	 *
	 * `redirected` (popups blocked → same-tab navigation) is the one outcome we
	 * must NOT clean up: the user is still mid-flow in this very tab and the
	 * callback will land on return, so deleting now would destroy a credential
	 * that's about to connect.
	 */
	const handleConnectAfterCreate = async (credentialId: string): Promise<void> => {
		toast({ title: 'Opening sign-in…' });
		const discard = async (): Promise<void> => {
			try {
				await deleteMutation.mutateAsync(credentialId);
			} catch {
				// Best-effort cleanup — surfaced below; the row stays listed if
				// the delete itself fails, which the user can remove manually.
			}
		};
		try {
			const outcome = await runConnectFlow(credentialId);
			switch (outcome.status) {
				case 'connected':
					toast({ title: 'Connected', variant: 'success' });
					void refetch();
					break;
				case 'redirected':
					break;
				case 'cancelled':
					await discard();
					toast({
						title: 'Sign-in cancelled',
						description: 'The unconnected credential was discarded.',
					});
					break;
				case 'timeout':
					await discard();
					toast({
						title: 'Sign-in timed out',
						description: 'The unconnected credential was discarded. Try again.',
						variant: 'error',
					});
					break;
			}
		} catch {
			await discard();
			toast({
				title: 'Could not complete sign-in',
				description: 'The unconnected credential was discarded.',
				variant: 'error',
			});
		}
	};

	const handleConnect = async (cred: Credential): Promise<void> => {
		toast({ title: `Opening sign-in for ${cred.name}…` });
		try {
			const outcome = await runConnectFlow(cred.credential_id);
			switch (outcome.status) {
				case 'connected':
					toast({ title: 'Connected', variant: 'success' });
					void refetch();
					break;
				case 'redirected':
					break;
				case 'cancelled':
					toast({ title: 'Connection cancelled' });
					break;
				case 'timeout':
					toast({
						title: 'Connection timed out',
						description: 'Finish the sign-in and refresh to see the result.',
						variant: 'error',
					});
					break;
			}
		} catch {
			toast({ title: 'Could not start the OAuth flow', variant: 'error' });
		}
	};

	const confirmDelete = (): void => {
		if (!deleteTarget) return;
		deleteMutation.mutate(deleteTarget.credential_id, {
			onSuccess: () => {
				toast({ title: 'Credential deleted', variant: 'success' });
				setDeleteTarget(null);
			},
		});
	};

	return (
		<PageShell spacing="space-y-0">
			<PageHeader
				title="Credentials"
				subtitle="Store and rotate the secrets your agents use to authenticate with external APIs."
				actions={
					<>
						<Button onClick={(): void => setCreateOpen(true)}>
							<Plus className="h-4 w-4" />
							Add credential
						</Button>
						<PageHelp
							title="About Credentials"
							intro="Credentials hold the secrets (tokens, API keys, OAuth grants) that let agents call external APIs on your behalf."
							sections={[
								{
									heading: 'Secrets are write-only',
									body: 'Once saved, a secret is redacted everywhere and never shown again — rotate it from the edit panel if you need a new value.',
								},
								{
									heading: 'OAuth credentials',
									body: 'OAuth 2.0 credentials use the Connect action to run the provider redirect flow and obtain tokens.',
								},
							]}
						/>
					</>
				}
			/>

			<CredentialsToolbar
				query={search}
				onQueryChange={setSearch}
				filter={typeFilter}
				onFilterChange={setTypeFilter}
				onRefresh={(): void => void refetch()}
				refreshing={isFetching}
			/>

			<div className="mt-4">
				<CredentialsList
					credentials={filtered}
					isLoading={isLoading}
					error={error as Error | null}
					onAdd={(): void => setCreateOpen(true)}
					onEdit={openEdit}
					onDelete={setDeleteTarget}
					onConnect={(cred): void => void handleConnect(cred)}
				/>
			</div>

			<CreateCredentialDialog
				open={createOpen}
				onClose={(): void => setCreateOpen(false)}
				onCreated={(info: CreatedCredentialInfo): void => {
					setCreateOpen(false);
					// Auto-open the sign-in flow only for OAuth2 credentials that
					// actually need a browser redirect (authorization-code grants with
					// an authorize URL). client_credentials and other non-redirect
					// grants are usable immediately and would 409 on connect.
					if (
						info.type === CredentialType.OAUTH2 &&
						info.provider !== 'static' &&
						info.needsConnect
					) {
						void handleConnectAfterCreate(info.credentialId);
					}
				}}
			/>

			<EditCredentialSheet
				credentialId={stickyEditId}
				open={editId != null}
				onClose={(): void => setEditId(null)}
				onAfterClose={(): void => setStickyEditId(null)}
			/>

			{deleteTarget != null && (
				<CascadeDeleteDialog
					open
					onClose={(): void => setDeleteTarget(null)}
					onConfirm={confirmDelete}
					entityType="credential"
					entityName={deleteTarget.name}
					loading={deleteMutation.isPending}
					error={deleteMutation.error}
				/>
			)}
		</PageShell>
	);
}
