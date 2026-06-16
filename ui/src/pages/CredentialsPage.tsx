import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import { Button } from '@/components/ui/Button';
import { CredentialsList } from '@/components/credentials';
import { CredentialEditSheet } from '@/components/credentials/CredentialEditSheet';
import { AddCredentialDialog } from '@/components/credentials/AddCredentialDialog';
import { useCredentialEditSheet } from '@/hooks/useCredentialEditSheet';
import { useAddCredentialDialog } from '@/hooks/useAddCredentialDialog';
import { useAuth } from '@/hooks/useAuth';
import { isTypingTarget } from '@/lib/keyboard';
import { subscribeCredentialImported } from '@/lib/events/credentialImported';

/**
 * Credentials page — thin shell.
 *
 * The page itself is now a few-dozen-line wrapper; the body is owned by
 * `<CredentialsList>` and per-row work lives in `<CredentialRow>`. This
 * matches the IA every other dashboard surface uses (`PageShell` +
 * `PageHeader` + `PageHelp` + `KeyboardShortcutsBar`) so users build
 * muscle memory across the product.
 *
 * Same-tab side effect — when a credential is imported on a different
 * surface (Discover's lazy-import path, the form page), the
 * `credentialImported` BroadcastChannel fires. We invalidate the
 * `['credentials']` query cache so the list refreshes without the user
 * having to navigate away and back. The `apiImported` channel is the
 * Workspace's concern — splitting the two is in Phase 4 of the revamp.
 */
export default function CredentialsPage() {
	const queryClient = useQueryClient();
	const { user } = useAuth();
	const editSheet = useCredentialEditSheet();
	const addDialog = useAddCredentialDialog();

	useEffect(() => {
		const unsubscribe = subscribeCredentialImported(() => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
		});
		return unsubscribe;
	}, [queryClient]);

	// `n` → open the Add Credential dialog (advertised in PageHelp /
	// KeyboardShortcutsBar). Skip while typing in a field, while a modifier is
	// held, or when the add dialog / edit sheet is already open.
	const addDialogOpen = addDialog.state.open;
	const editSheetOpen = editSheet.open;
	const openAddDialog = addDialog.openWorkspace;
	useEffect(() => {
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key !== 'n' || e.metaKey || e.ctrlKey || e.altKey) return;
			if (isTypingTarget(e.target)) return;
			if (addDialogOpen || editSheetOpen) return;
			e.preventDefault();
			openAddDialog();
		};
		document.addEventListener('keydown', onKeyDown);
		return () => document.removeEventListener('keydown', onKeyDown);
	}, [openAddDialog, addDialogOpen, editSheetOpen]);

	return (
		<>
			<PageShell spacing="space-y-5" className="md:pb-12">
				<PageHeader
					title="Credentials"
					subtitle="Stored secrets your agents use to call upstream APIs."
					actions={
						<>
							<Button onClick={() => addDialog.openWorkspace()}>
								<Plus className="h-4 w-4" /> Add Credential
							</Button>
							<PageHelp
								title="About Credentials"
								intro={
									<p>
										Credentials are the secrets your agents present when calling
										an upstream API — bearer tokens, API keys, OAuth
										connections. Jentic stores them encrypted and never returns
										the raw value once written; agents call through the broker,
										which injects the right header on each upstream request.
									</p>
								}
								sections={[
									{
										heading: 'Reading the list',
										body: (
											<p>
												The dot next to each credential is its{' '}
												<strong>health</strong>. Green means the broker has
												used it successfully recently; red means the
												upstream rejected it (commonly an expired OAuth
												grant — use <strong>Reconnect</strong> to fix); a
												muted dot means we haven't tried it yet (use{' '}
												<strong>Test connection</strong> on the edit page).
												Each row also shows which toolkits have it bound, so
												you can trace credentials → toolkits → agents at a
												glance.
											</p>
										),
									},
									{
										heading: 'Manual vs OAuth',
										body: (
											<p>
												<strong>Credentials</strong> at the top are
												manually-stored secrets — you paste a value, you
												rotate it. <strong>OAuth connections</strong> below
												are managed by Pipedream — you reconnect through
												their flow when the grant expires. Enable Pipedream
												once and any "OAuth" auth type on imported APIs will
												route through it automatically.
											</p>
										),
									},
									{
										heading: 'Adding credentials',
										body: (
											<p>
												Click <strong>Add Credential</strong> to pick an API
												and paste a value. Importing an API from{' '}
												<strong>Discover</strong> with a "Connect" CTA also
												lands you here pre-filled. Catalog APIs are imported
												into your workspace as a side effect of the first
												credential add — no separate import step.
											</p>
										),
									},
								]}
								shortcuts={[
									{ keys: ['n'], label: 'Add credential' },
									{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
								]}
							/>
						</>
					}
				/>

				<CredentialsList
					loggedIn={!!user?.logged_in}
					onEditCredential={(cred) => editSheet.openSheet(cred.id)}
					onAddCredential={() => addDialog.openWorkspace()}
				/>
			</PageShell>

			<CredentialEditSheet
				credentialId={editSheet.stickyId}
				open={editSheet.open}
				onClose={editSheet.closeSheet}
				onAfterClose={editSheet.clearSticky}
			/>

			<AddCredentialDialog
				state={addDialog.state}
				onClose={addDialog.close}
				onGoToStep={addDialog.goToStep}
				onSelectApi={addDialog.setSelectedApi}
				onSavedCredentialId={addDialog.setSavedCredentialId}
			/>

			<KeyboardShortcutsBar
				shortcuts={[
					{ keys: ['n'], label: 'add' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'help' },
				]}
			/>
		</>
	);
}
