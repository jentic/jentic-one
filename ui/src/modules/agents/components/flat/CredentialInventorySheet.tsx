/**
 * CredentialInventorySheet — the org-wide credential inventory, opened from the
 * page header because the dock is agent-scoped. The kit lives in
 * `shared/credentials/`, so this sheet is composition.
 *
 * It owns the Unbound filter: a credential no agent is bound to appears on no
 * agent's screen. Unbound is proved by inverting the whole fleet's bindings, and
 * withheld while that join cannot prove it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Filter, Key, Plus, X } from 'lucide-react';
import {
	Button,
	EmptyState,
	RefreshButton,
	SearchInput,
	SegmentedToggle,
	SheetPrimitive,
	toast,
} from '@/shared/ui';
import { useEagerCursorDrain } from '@/shared/hooks';
import {
	useAgents,
	useAgentsCredentialBindings,
	useCredentialUsageTotals,
	useInvalidateCredentialBindingSurfaces,
	useRefreshFleetCredentialBindings,
} from '@/modules/agents/api';
import {
	CREDENTIAL_TYPE_LABELS,
	CREDENTIAL_TYPE_ORDER,
	CredentialType,
	useAllCredentials,
	useDeleteCredential,
	useRunConnectFlow,
	type Credential,
} from '@/shared/credentials/api';
import { CredentialsList } from '@/shared/credentials/components/CredentialsList';
import type { CredentialTypeFilter } from '@/shared/credentials/components/CredentialsToolbar';
import {
	CreateCredentialFlow,
	type CreatedCredentialInfo,
} from '@/shared/credentials/components/CreateCredentialFlow';
import { CredentialDeleteDialog } from '@/shared/credentials/components/CredentialDeleteDialog';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

const FILTER_OPTIONS: { value: CredentialTypeFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	...CREDENTIAL_TYPE_ORDER.map((type) => ({
		value: type as CredentialTypeFilter,
		label: CREDENTIAL_TYPE_LABELS[type],
	})),
];

/** Which credentials the list is narrowed to by fleet usage. */
type BindingFilter = 'any' | 'unbound';

export function CredentialInventorySheet({
	open,
	onClose,
	autoOpenCreate = false,
}: {
	open: boolean;
	onClose: () => void;
	/** Open onto the create wizard — for a caller whose own label promised a new
	 * credential. */
	autoOpenCreate?: boolean;
}) {
	const headingId = 'credential-inventory-sheet-title';

	const [search, setSearch] = useState('');
	const [typeFilter, setTypeFilter] = useState<CredentialTypeFilter>('all');
	const [bindingFilter, setBindingFilter] = useState<BindingFilter>('any');
	const [createOpen, setCreateOpen] = useState(false);
	const [editId, setEditId] = useState<string | null>(null);
	const [stickyEditId, setStickyEditId] = useState<string | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<Credential | null>(null);

	// Spent once per opening: cancelling the wizard leaves the operator in the
	// inventory, and reopening it would trap them in a form they just dismissed.
	const createSignalSpent = useRef(false);
	useEffect(() => {
		if (!open) {
			createSignalSpent.current = false;
			return;
		}
		if (!autoOpenCreate || createSignalSpent.current) return;
		createSignalSpent.current = true;
		setCreateOpen(true);
	}, [open, autoOpenCreate]);

	// Every page, not just the first: the header promises "Every credential in this
	// workspace", and the Unbound count is taken over the whole inventory.
	const credentialsSource = useAllCredentials();
	const deleteMutation = useDeleteCredential();
	// No agent id: this sheet is the ORG-wide inventory, and every sweep it needs
	// is the `'credential'` scope, which is agent-agnostic by construction.
	const invalidateBindingSurfaces = useInvalidateCredentialBindingSurfaces(null);
	// A successful sign-in invalidates the whole credentials slice, so the flat
	// surface's tiles and strip hints refresh along with this sheet's list.
	const runConnect = useRunConnectFlow();

	// Which credentials the fleet uses, by inverting every agent's binding list — the
	// same reads the agents surface already made. Archived agents are outside the
	// join: archiving sweeps their bindings. Gated on `open`.
	const fleet = useAgents({ status: 'all', enabled: open });
	const {
		fetchNextPage,
		hasNextPage,
		isFetchingNextPage,
		isError: fleetError,
		isPending: fleetPending,
	} = fleet;
	useEagerCursorDrain({
		hasNextPage: open && hasNextPage,
		isFetchingNextPage,
		isError: fleetError,
		fetchNextPage,
	});
	const fleetAgentIds = useMemo(
		() =>
			(fleet.data?.pages.flatMap((p) => p.entities) ?? [])
				.filter((a) => a.status !== 'archived')
				.map((a) => a.id),
		[fleet.data],
	);
	const bindingsByAgent = useAgentsCredentialBindings(open ? fleetAgentIds : []);
	const refreshFleetBindings = useRefreshFleetCredentialBindings();

	// Credential id → how many agents hold it. `null` = cannot be proved, which is
	// NOT "nothing is bound": a partial fleet makes a bound credential look unbound.
	const agentsPerCredential = useMemo(() => {
		if (fleetPending || hasNextPage || fleetError) return null;
		if (fleetAgentIds.some((id) => !bindingsByAgent.has(id))) return null;
		const counts = new Map<string, number>();
		for (const bindings of bindingsByAgent.values()) {
			// One agent counts once per credential even when it holds several
			// bindings: the figure answers "how many agents", not "how many bindings".
			for (const credentialId of new Set(bindings.map((b) => b.credentialId))) {
				counts.set(credentialId, (counts.get(credentialId) ?? 0) + 1);
			}
		}
		return counts;
	}, [fleetPending, hasNextPage, fleetError, fleetAgentIds, bindingsByAgent]);
	const unboundUnknown = bindingFilter === 'unbound' && agentsPerCredential == null;
	// The roster drain is the one phase provably still in flight, so anything past it
	// omits the figure rather than pulsing a skeleton forever.
	const fleetJoinLoading = fleetPending || hasNextPage || isFetchingNextPage;

	const credentials = credentialsSource.items;

	// Counted over the whole inventory, not the search-narrowed view, so the
	// inventory drain must be whole too — not merely the fleet join.
	const unboundCount = useMemo(() => {
		if (agentsPerCredential == null || !credentialsSource.complete) return null;
		return credentials.filter((c) => !agentsPerCredential.has(c.credential_id)).length;
	}, [agentsPerCredential, credentialsSource.complete, credentials]);
	const bindingFilterOptions = useMemo(
		() => [
			{ value: 'any' as BindingFilter, label: 'Any agent' },
			{
				value: 'unbound' as BindingFilter,
				label: unboundCount == null ? 'Unbound' : `Unbound (${unboundCount})`,
			},
		],
		[unboundCount],
	);
	const filtered = useMemo(() => {
		const q = search.trim().toLowerCase();
		return credentials.filter((c) => {
			if (typeFilter !== 'all' && c.type !== typeFilter) return false;
			if (bindingFilter === 'unbound' && agentsPerCredential?.has(c.credential_id) === true) {
				return false;
			}
			if (!q) return true;
			return (
				c.name.toLowerCase().includes(q) ||
				c.api.vendor?.toLowerCase().includes(q) ||
				c.provider?.toLowerCase().includes(q)
			);
		});
	}, [credentials, search, typeFilter, bindingFilter, agentsPerCredential]);

	// Both usage figures are three-state with different gates: the fleet count is
	// exact or withheld; a credential missing from a TRUNCATED top-N is unknown.
	const usage = useCredentialUsageTotals(open);
	const usageFor = useCallback(
		(cred: Credential) => ({
			usedByAgentCount:
				agentsPerCredential?.get(cred.credential_id) ??
				(agentsPerCredential != null ? 0 : fleetJoinLoading ? undefined : null),
			callsLast7d: usage.isLoading
				? undefined
				: usage.data == null
					? null
					: (usage.data.totals.get(cred.credential_id) ??
						(usage.data.complete ? 0 : null)),
		}),
		[agentsPerCredential, fleetJoinLoading, usage.isLoading, usage.data],
	);

	// The nested sheets each have a document-level Escape handler firing on the same
	// keydown, so the guard reads still-unflushed state. The delete confirm needs none.
	const nestedSheetOpen = editId != null || createOpen;
	const guardedClose = (): void => {
		if (nestedSheetOpen) return;
		onClose();
	};

	const openEdit = (cred: Credential): void => {
		setStickyEditId(cred.credential_id);
		setEditId(cred.credential_id);
	};

	// Mirrors CredentialsPage.handleConnectAfterCreate: an abandoned OAuth handshake
	// discards the credential. `redirected` must NOT clean up — the user is mid-flow.
	const handleConnectAfterCreate = async (credentialId: string): Promise<void> => {
		toast({ title: 'Opening sign-in…' });
		const discard = async (): Promise<void> => {
			try {
				await deleteMutation.mutateAsync(credentialId);
			} catch {
				// Best-effort cleanup; the row stays listed if the delete fails.
			}
		};
		try {
			const outcome = await runConnect(credentialId);
			switch (outcome.status) {
				case 'connected':
					// The connect hook invalidated the credentials slice, which
					// refetches this sheet's list along with every other join.
					toast({ title: 'Connected', variant: 'success' });
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

	// Mirrors CredentialsPage.handleConnect (standalone connect keeps the row).
	const handleConnect = async (cred: Credential): Promise<void> => {
		toast({ title: `Opening sign-in for ${cred.name}…` });
		try {
			const outcome = await runConnect(cred.credential_id);
			switch (outcome.status) {
				case 'connected':
					toast({ title: 'Connected', variant: 'success' });
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
		const credentialId = deleteTarget.credential_id;
		deleteMutation.mutate(credentialId, {
			onSuccess: () => {
				// The delete hook refreshes the credentials slice only, but the surface behind
				// keeps a binding query per agent — org-wide scope, since every agent loses it.
				invalidateBindingSurfaces(credentialId, 'credential');
				toast({ title: 'Credential deleted', variant: 'success' });
				setDeleteTarget(null);
			},
		});
	};

	return (
		<>
			<SheetPrimitive
				open={open}
				onClose={guardedClose}
				ariaLabelledBy={headingId}
				className="sm:w-[640px] xl:w-[880px]"
			>
				<div className="flex h-full flex-col">
					<header className="border-border flex items-start justify-between gap-3 border-b px-5 py-4">
						<div className="min-w-0">
							<h2 id={headingId} className="text-foreground text-base font-semibold">
								Credentials
							</h2>
							<p className="text-muted-foreground text-xs">
								Every credential in this workspace — any agent can be bound to them.
							</p>
						</div>
						<div className="flex shrink-0 items-center gap-2">
							<Button size="sm" onClick={(): void => setCreateOpen(true)}>
								<Plus className="h-4 w-4" />
								Add credential
							</Button>
							<Button
								variant="ghost"
								size="sm"
								aria-label="Close"
								onClick={onClose}
								className="text-muted-foreground hover:text-foreground"
							>
								<X className="h-4 w-4" />
							</Button>
						</div>
					</header>

					<div className="border-border flex flex-wrap items-center gap-2 border-b px-5 py-3">
						{/* A credential bound to zero agents is reachable from no agent's screen —
						    the sheet's one unique power, so it leads the toolbar. */}
						<div className="flex shrink-0 items-center gap-2">
							<span className="text-foreground text-xs font-semibold">Used by</span>
							<SegmentedToggle<BindingFilter>
								options={bindingFilterOptions}
								value={bindingFilter}
								onChange={setBindingFilter}
								layoutId="credential-inventory-binding-filter"
								ariaLabel="Filter by agent usage"
								className="border-primary/30 bg-primary/5"
							/>
						</div>
						<SearchInput
							value={search}
							onValueChange={setSearch}
							icon={<Filter className="h-3.5 w-3.5" />}
							placeholder="Filter credentials…"
							aria-label="Filter credentials"
							disabled={!credentialsSource.isPending && credentials.length === 0}
							className="min-w-40 flex-1"
						/>
						<SegmentedToggle<CredentialTypeFilter>
							options={FILTER_OPTIONS}
							value={typeFilter}
							onChange={setTypeFilter}
							layoutId="credential-inventory-type-filter"
							ariaLabel="Filter by credential type"
						/>
						<RefreshButton
							onRefresh={credentialsSource.refresh}
							pending={credentialsSource.isFetching}
						/>
					</div>

					<div className="flex-1 overflow-y-auto px-5 py-4">
						{unboundUnknown ? (
							// Withheld, not guessed: the list would read as "used by nobody",
							// and the next move on that reading is to delete them.
							<div
								role="status"
								className="border-border bg-muted/40 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3"
							>
								<p className="text-muted-foreground min-w-0 flex-1 text-sm">
									Which credentials no agent uses can&rsquo;t be shown yet — that
									answer needs every agent&rsquo;s bindings, and some
									haven&rsquo;t come back. Switch to <strong>Any agent</strong> to
									see the full inventory meanwhile.
								</p>
								<Button
									variant="secondary"
									size="sm"
									onClick={(): void => {
										if (fleetError || hasNextPage) void fetchNextPage();
										refreshFleetBindings();
									}}
								>
									Try again
								</Button>
							</div>
						) : (
							<CredentialsList
								credentials={filtered}
								isLoading={credentialsSource.isPending}
								error={credentialsSource.error}
								onAdd={(): void => setCreateOpen(true)}
								onEdit={openEdit}
								onDelete={setDeleteTarget}
								onConnect={(cred): void => void handleConnect(cred)}
								// A drawer is not a page: three columns inside it are
								// what clip a credential's name mid-word.
								columns={2}
								usageFor={usageFor}
								emptyState={
									credentials.length > 0 ? (
										<EmptyState
											icon={<Key className="h-10 w-10 opacity-30" />}
											title={
												bindingFilter === 'unbound'
													? 'Every credential is in use'
													: 'No credentials match'
											}
											description={
												bindingFilter === 'unbound'
													? 'Each credential here is bound to at least one agent — none are sitting unused.'
													: 'No credential matches this filter. Widen it to see the rest of the inventory.'
											}
										/>
									) : undefined
								}
							/>
						)}
					</div>
				</div>
			</SheetPrimitive>

			{/* Conditionally mounted: the wizard's provider/API text must not sit in
			    the host surface's DOM while it is closed. */}
			{createOpen && (
				<CreateCredentialFlow
					open
					onClose={(): void => setCreateOpen(false)}
					onCreated={(info: CreatedCredentialInfo): void => {
						setCreateOpen(false);
						if (
							info.type === CredentialType.OAUTH2 &&
							info.provider !== 'static' &&
							info.needsConnect
						) {
							void handleConnectAfterCreate(info.credentialId);
						}
					}}
				/>
			)}

			<EditCredentialSheet
				credentialId={stickyEditId}
				open={editId != null}
				onClose={(): void => setEditId(null)}
				onAfterClose={(): void => setStickyEditId(null)}
				// A bound-agent link lands on the surface this sheet is covering,
				// so following one closes the whole stack down to it.
				onNavigateAway={(): void => {
					setEditId(null);
					onClose();
				}}
			/>

			{deleteTarget != null && (
				// Org-wide delete: the confirm names the agents that lose access.
				<CredentialDeleteDialog
					open
					credentialId={deleteTarget.credential_id}
					credentialName={deleteTarget.name}
					onClose={(): void => setDeleteTarget(null)}
					onConfirm={confirmDelete}
					loading={deleteMutation.isPending}
					error={deleteMutation.error}
				/>
			)}
		</>
	);
}
