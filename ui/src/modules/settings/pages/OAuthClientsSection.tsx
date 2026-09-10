/**
 * OAuthClientsSection — orchestration for the OAuth-clients settings surface.
 *
 * Composition only (the rebuild plan's decomposition): the roster
 * (`ClientsTable`), the D7 approval queue (`ApprovalQueue`), the detail
 * console (`ClientDetailSheet`), the create/edit form (`ClientFormSheet`),
 * and the section-level confirm/secret dialogs. Data wiring stays in
 * `settings/api/hooks.ts`.
 *
 * The page chrome — header, "Add client", PageHelp, and the Clients/Queue
 * TabNav — lives one level UP in `SettingsPage` (flattened per review: one
 * tab bar, no double headers). This component is a controlled panel: the
 * page passes the active tab down and the create-sheet open state is lifted
 * so the header action can open it; the queue's pending/denied filter stays
 * here so a denied roster row's "Review in queue" verb lands directly on the
 * Denied slice.
 */
import { useState } from 'react';
import { toast } from '@/shared/ui';
import {
	useDeactivateOAuthClient,
	useOAuthClients,
	useReactivateOAuthClient,
	useRotateOAuthClientSecret,
	type OAuthClient,
} from '@/modules/settings/api/hooks';
import { ClientsTable, type ClientAction } from '@/modules/settings/components/ClientsTable';
import { ApprovalQueue, type QueueFilter } from '@/modules/settings/components/ApprovalQueue';
import { ClientDetailSheet } from '@/modules/settings/components/ClientDetailSheet';
import { ClientFormSheet } from '@/modules/settings/components/ClientFormSheet';
import { McpConnectCard } from '@/modules/settings/components/McpConnectCard';
import {
	DeactivateConfirmDialog,
	RotateConfirmDialog,
	SecretDialog,
} from '@/modules/settings/components/ClientLifecycleDialogs';

export const SECTION_TABS = ['clients', 'queue'] as const;
export type SectionTab = (typeof SECTION_TABS)[number];

export function isSectionTab(value: string | null): value is SectionTab {
	return SECTION_TABS.includes(value as SectionTab);
}

export interface OAuthClientsSectionProps {
	/** The page-owned tab (mirrors `?tab=`; see SettingsPage). */
	activeTab: SectionTab;
	/** Page-owned tab setter — "Review in queue" retargets through it. */
	onTabChange: (tab: SectionTab) => void;
	/** Lifted create-sheet state so the header's "Add client" can open it. */
	createOpen: boolean;
	onCreateOpenChange: (open: boolean) => void;
}

export function OAuthClientsSection({
	activeTab,
	onTabChange,
	createOpen,
	onCreateOpenChange,
}: OAuthClientsSectionProps) {
	const [queueFilter, setQueueFilter] = useState<QueueFilter>('pending');

	// One include_inactive read backs the roster: the status segments partition
	// the joint (approval_status, active) state client-side — including the
	// approved+inactive zombies the old "Show inactive" checkbox hid (#1312).
	const clientsQuery = useOAuthClients(true);

	// Sheet/dialog targets. Target + open are separate for the sheets so a
	// dismissal keeps the mounted component (and any draft) alive. (The
	// create sheet's open state is the lifted `createOpen` prop.)
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
				onTabChange('queue');
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

	return (
		<section>
			{activeTab === 'queue' && (
				<ApprovalQueue filter={queueFilter} onFilterChange={setQueueFilter} />
			)}

			{activeTab === 'clients' && (
				<div className="space-y-4">
					{/* Deployment-level MCP pointer (#1249) — MCP clients using
					    interactive OAuth register into exactly this roster. */}
					<McpConnectCard />
					<ClientsTable
						clients={clientsQuery.data}
						isLoading={clientsQuery.isLoading}
						isFetching={clientsQuery.isFetching}
						error={clientsQuery.error}
						onRefresh={(): void => void clientsQuery.refetch()}
						onOpenDetail={openDetail}
						onAction={handleAction}
						onCreate={(): void => onCreateOpenChange(true)}
						pendingId={pendingId}
					/>
				</div>
			)}

			{/* Sheets are mounted persistently (dialog-state rule): a casual
			    dismiss keeps the form draft; only a committed create resets it. */}
			<ClientFormSheet
				open={createOpen}
				onClose={(): void => onCreateOpenChange(false)}
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
				onReactivate={(client): void => void handleReactivate(client)}
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
