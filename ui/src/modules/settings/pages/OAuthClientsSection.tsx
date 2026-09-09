/**
 * OAuthClientsSection — orchestration for the OAuth-clients settings surface.
 *
 * Composition only (the rebuild plan's decomposition): the roster
 * (`ClientsTable`), the D7 approval queue (`ApprovalQueue`), the detail
 * console (`ClientDetailSheet`), the create/edit form (`ClientFormSheet`),
 * and the section-level confirm/secret dialogs. Data wiring stays in
 * `settings/api/hooks.ts`.
 *
 * The active tab lives in `?tab=` so the agent rail's "Review" action on an
 * `oauth_client.registered` alert can deep-link straight to the queue; the
 * queue's pending/denied filter is lifted here so a denied roster row's
 * "Review in queue" verb lands directly on the Denied slice.
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router';
import { KeyRound, Plus, ShieldQuestion } from 'lucide-react';
import { Button, PageHelp, TabNav, toast, type TabNavOption } from '@/shared/ui';
import {
	useDeactivateOAuthClient,
	useOAuthClientQueue,
	useOAuthClients,
	useReactivateOAuthClient,
	useRotateOAuthClientSecret,
	type OAuthClient,
} from '@/modules/settings/api/hooks';
import { ClientsTable, type ClientAction } from '@/modules/settings/components/ClientsTable';
import { ApprovalQueue, type QueueFilter } from '@/modules/settings/components/ApprovalQueue';
import { ClientDetailSheet } from '@/modules/settings/components/ClientDetailSheet';
import { ClientFormSheet } from '@/modules/settings/components/ClientFormSheet';
import {
	DeactivateConfirmDialog,
	RotateConfirmDialog,
	SecretDialog,
} from '@/modules/settings/components/ClientLifecycleDialogs';

const SECTION_TABS = ['clients', 'queue'] as const;
type SectionTab = (typeof SECTION_TABS)[number];

function isSectionTab(value: string | null): value is SectionTab {
	return SECTION_TABS.includes(value as SectionTab);
}

export function OAuthClientsSection() {
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: SectionTab = isSectionTab(tabParam) ? tabParam : 'clients';
	// Fetched at section level so the queue tab label can carry the pending
	// count even while the clients tab is active.
	const { data: pendingClients } = useOAuthClientQueue('pending');
	const [queueFilter, setQueueFilter] = useState<QueueFilter>('pending');

	const setTab = (tab: SectionTab): void => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'clients') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: false },
		);
	};

	// One include_inactive read backs the roster: the status segments partition
	// the joint (approval_status, active) state client-side — including the
	// approved+inactive zombies the old "Show inactive" checkbox hid (#1312).
	const clientsQuery = useOAuthClients(true);

	// Sheet/dialog targets. Target + open are separate for the sheets so a
	// dismissal keeps the mounted component (and any draft) alive.
	const [createOpen, setCreateOpen] = useState(false);
	const [editTarget, setEditTarget] = useState<OAuthClient | null>(null);
	const [editOpen, setEditOpen] = useState(false);
	const [detailTarget, setDetailTarget] = useState<OAuthClient | null>(null);
	const [detailOpen, setDetailOpen] = useState(false);
	const [deactivateTarget, setDeactivateTarget] = useState<OAuthClient | null>(null);
	const [rotateTarget, setRotateTarget] = useState<OAuthClient | null>(null);
	const [revealedSecret, setRevealedSecret] = useState<string | null>(null);
	const [secretDialogTitle, setSecretDialogTitle] = useState('Client Secret');

	const deactivateMutation = useDeactivateOAuthClient();
	const reactivateMutation = useReactivateOAuthClient();
	const rotateMutation = useRotateOAuthClientSecret();
	const inFlight = [deactivateMutation, reactivateMutation, rotateMutation].find(
		(m) => m.isPending,
	);
	const pendingId = typeof inFlight?.variables === 'string' ? inFlight.variables : null;

	const openDetail = (client: OAuthClient): void => {
		setDetailTarget(client);
		setDetailOpen(true);
	};

	const openEdit = (client: OAuthClient): void => {
		// Close the detail sheet first — two stacked right-side sheets would
		// double the focus traps and both would answer the same Escape.
		setDetailOpen(false);
		setEditTarget(client);
		setEditOpen(true);
	};

	const handleReactivate = async (client: OAuthClient): Promise<void> => {
		try {
			await reactivateMutation.mutateAsync(client.id);
			toast({ title: `${client.name} reactivated`, variant: 'success' });
		} catch (err) {
			toast({
				title: 'Failed to reactivate client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleAction = (client: OAuthClient, action: ClientAction): void => {
		switch (action) {
			case 'edit':
				openEdit(client);
				break;
			case 'rotate':
				setRotateTarget(client);
				break;
			case 'deactivate':
				setDeactivateTarget(client);
				break;
			case 'reactivate':
				// Only reachable for approved+inactive rows (`canReactivate`) —
				// a denied row's recovery is the queue's Approve verb instead.
				void handleReactivate(client);
				break;
			case 'review-in-queue':
				setQueueFilter('denied');
				setTab('queue');
				break;
		}
	};

	const handleDeactivate = async (): Promise<void> => {
		if (!deactivateTarget) return;
		try {
			await deactivateMutation.mutateAsync(deactivateTarget.id);
			toast({ title: 'OAuth client deactivated', variant: 'success' });
			setDeactivateTarget(null);
		} catch (err) {
			toast({
				title: 'Failed to deactivate client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleRotateConfirm = async (): Promise<void> => {
		if (!rotateTarget) return;
		try {
			const result = await rotateMutation.mutateAsync(rotateTarget.id);
			setRotateTarget(null);
			setSecretDialogTitle(`New Secret for ${rotateTarget.name}`);
			setRevealedSecret(result.client_secret);
		} catch (err) {
			toast({
				title: 'Failed to rotate secret',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const tabOptions: TabNavOption<SectionTab>[] = [
		{ value: 'clients', label: 'Clients', icon: <KeyRound className="h-4 w-4" /> },
		{
			value: 'queue',
			label: 'Approval queue',
			icon: <ShieldQuestion className="h-4 w-4" />,
			count: pendingClients?.length || undefined,
		},
	];

	return (
		<section>
			<div className="mb-6 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
				<div className="min-w-0 flex-1">
					<h2 className="text-foreground text-lg font-semibold tracking-tight">
						OAuth Clients
					</h2>
					<p className="text-muted-foreground mt-0.5 text-sm">
						Manage third-party applications that can authenticate users via Jentic One.
					</p>
				</div>
				<div className="flex shrink-0 items-center gap-2 self-center">
					<Button onClick={(): void => setCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						Add client
					</Button>
					<PageHelp
						title="About OAuth Clients"
						intro="OAuth clients are third-party applications that use Jentic One for user authentication."
						sections={[
							{
								heading: 'Client ID',
								body: 'The client_id is a public identifier used in OAuth flows. Configure it in the third-party application.',
							},
							{
								heading: 'Client Secret',
								body: 'The client secret is shown once at creation and after rotation. Store it securely — it cannot be retrieved later.',
							},
							{
								heading: 'Redirect URIs',
								body: 'OAuth callbacks are only allowed to URLs in this list. Include all environments (dev, staging, prod).',
							},
							{
								heading: 'Approval queue',
								body: 'Clients that register themselves (DCR) wait in the queue until an admin approves them. Judge a registration by its redirect-URI origins — the name is self-reported.',
							},
						]}
					/>
				</div>
			</div>

			<TabNav<SectionTab>
				options={tabOptions}
				value={activeTab}
				onChange={setTab}
				ariaLabel="OAuth client sections"
				className="mb-4"
			/>

			{activeTab === 'queue' && (
				<ApprovalQueue filter={queueFilter} onFilterChange={setQueueFilter} />
			)}

			{activeTab === 'clients' && (
				<ClientsTable
					clients={clientsQuery.data}
					isLoading={clientsQuery.isLoading}
					isFetching={clientsQuery.isFetching}
					error={clientsQuery.error}
					onRefresh={(): void => void clientsQuery.refetch()}
					onOpenDetail={openDetail}
					onAction={handleAction}
					onCreate={(): void => setCreateOpen(true)}
					pendingId={pendingId}
				/>
			)}

			{/* Sheets are mounted persistently (dialog-state rule): a casual
			    dismiss keeps the form draft; only a committed create resets it. */}
			<ClientFormSheet
				open={createOpen}
				onClose={(): void => setCreateOpen(false)}
				onSecretRevealed={(secret): void => {
					setSecretDialogTitle('Client Secret Created');
					setRevealedSecret(secret);
				}}
			/>
			<ClientFormSheet
				open={editOpen}
				onClose={(): void => setEditOpen(false)}
				client={editTarget}
			/>
			<ClientDetailSheet
				client={detailTarget}
				open={detailOpen}
				onClose={(): void => setDetailOpen(false)}
				onEdit={openEdit}
				onRotate={setRotateTarget}
				onDeactivate={setDeactivateTarget}
			/>

			{/* Stateless confirms — conditional mounting is fine here. */}
			{deactivateTarget != null && (
				<DeactivateConfirmDialog
					open
					onClose={(): void => setDeactivateTarget(null)}
					onConfirm={(): void => void handleDeactivate()}
					isPending={deactivateMutation.isPending}
					clientName={deactivateTarget.name}
					error={deactivateMutation.error}
				/>
			)}
			{rotateTarget != null && (
				<RotateConfirmDialog
					open
					onClose={(): void => setRotateTarget(null)}
					onConfirm={(): void => void handleRotateConfirm()}
					isPending={rotateMutation.isPending}
					clientName={rotateTarget.name}
				/>
			)}
			{/* Sensitive-data exception: unmounting on close wipes the secret. */}
			{revealedSecret != null && (
				<SecretDialog
					open
					onClose={(): void => {
						setRevealedSecret(null);
						rotateMutation.reset();
					}}
					secret={revealedSecret}
					title={secretDialogTitle}
				/>
			)}
		</section>
	);
}
