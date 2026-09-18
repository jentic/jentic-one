/**
 * CredentialInventorySheet — the org-wide credential inventory (D1 as
 * amended by D20, plan §4.6) opened from the page-level Credentials control
 * on the Agents page header (the dock is agent-scoped only; page level is
 * where org-wide surfaces live). The whole kit already
 * lives in `shared/credentials/` (list, create wizard, edit sheet, delete
 * confirm, connect flow), so this sheet is pure composition — the handlers
 * mirror `CredentialsPage` so behaviour can't drift while both surfaces are
 * live.
 *
 * It also owns the **Unbound** filter: a credential no agent is bound to
 * appears on no agent's screen, so this inventory is its only home. "Unbound"
 * is proved by inverting the whole fleet's bindings — the reads the agents
 * surface already holds in cache — and withheld entirely while that join
 * cannot prove it, because an incomplete join can only under-count bindings,
 * i.e. present a live, in-use secret as unused.
 *
 * Container notes:
 *  - The page's sticky `CredentialsToolbar` is page chrome (it pins under
 *    the TopNavbar); inside a sheet its offsets are wrong, so the sheet
 *    renders the same Filter affordance from the same primitives instead
 *    (`SearchInput` + `SegmentedToggle`, per the status-and-filter rule).
 *  - The delete confirm is a native modal dialog — `SheetPrimitive` yields
 *    Escape to it while it's open. The create flow and the edit sheet each
 *    stack as a second `SheetPrimitive`, so closing THIS sheet is guarded
 *    while either is open so their Escape doesn't cascade.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Filter, Key, Plus, X } from 'lucide-react';
import {
	Button,
	CascadeDeleteDialog,
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
	useRefreshFleetCredentialBindings,
} from '@/modules/agents/api';
import {
	CREDENTIAL_TYPE_LABELS,
	CREDENTIAL_TYPE_ORDER,
	CredentialType,
	useCredentials,
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
	/**
	 * Open onto the create wizard — for a caller whose own label promised a new
	 * credential (`?credentials=new`, the dashboard's "Add a credential").
	 */
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
	// inventory, which is a legitimate place to be — reopening it would trap
	// them in a form they just dismissed.
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

	const { data, isLoading, error, refetch, isFetching } = useCredentials();
	const deleteMutation = useDeleteCredential();
	// Cache-aware connect: a successful sign-in invalidates the whole
	// credentials slice, so the flat surface's tile grid / strip hints
	// (joined off the separate drained listAll query) refresh along with
	// this sheet's own list.
	const runConnect = useRunConnectFlow();

	// Which credentials the fleet actually uses, by inverting every agent's
	// binding list — the same reads the agents surface already made, so this
	// costs no extra round-trip and never fans out to a per-credential
	// `GET /credentials/{id}/agents`. Archived agents are rightly outside the
	// join: archiving sweeps their bindings.
	//
	// All of it is gated on `open`: the host mounts this sheet for the whole
	// life of the page, and a closed sheet has no question to answer — it must
	// not drain the roster or fan out N binding reads behind the operator's
	// back, nor run a second drain against the one the surface below already
	// has in flight.
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

	// Credential id → how many agents hold it. `null` = cannot be proved, which
	// is NOT the same as "nothing is bound". A partial fleet or a missing
	// bindings list can only make a bound credential look unbound — the one
	// error that invites deleting a secret something is using — so the filter
	// withholds its answer instead.
	const agentsPerCredential = useMemo(() => {
		if (fleetPending || hasNextPage || fleetError) return null;
		if (fleetAgentIds.some((id) => !bindingsByAgent.has(id))) return null;
		const counts = new Map<string, number>();
		for (const bindings of bindingsByAgent.values()) {
			// One agent counts once per credential even when it holds several
			// bindings to it: the figure answers "how many agents", not "how
			// many bindings".
			for (const credentialId of new Set(bindings.map((b) => b.credentialId))) {
				counts.set(credentialId, (counts.get(credentialId) ?? 0) + 1);
			}
		}
		return counts;
	}, [fleetPending, hasNextPage, fleetError, fleetAgentIds, bindingsByAgent]);
	const unboundUnknown = bindingFilter === 'unbound' && agentsPerCredential == null;
	// The roster drain is the one phase that is provably still in flight; a
	// bindings list that never arrives is indistinguishable from one still on
	// the way, so anything past the drain omits the figure rather than leaving
	// a skeleton pulsing over data that may never come.
	const fleetJoinLoading = fleetPending || hasNextPage || isFetchingNextPage;

	const credentials = useMemo(() => data?.data ?? [], [data]);

	// How many credentials no agent uses — the same gate: a count is only
	// offered when the fleet join can prove it, never estimated from a partial
	// one. Counted over the whole inventory, not the search-narrowed view, so
	// the number answers "how many are sitting unused" rather than "how many of
	// what I'm looking at".
	const unboundCount = useMemo(() => {
		if (agentsPerCredential == null) return null;
		return credentials.filter((c) => !agentsPerCredential.has(c.credential_id)).length;
	}, [agentsPerCredential, credentials]);
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

	// The cards' two usage figures, resolved per credential. Both are
	// three-state, and the gates differ: the fleet count is exact or withheld,
	// while the call volume comes off a top-N leaderboard — so a credential
	// missing from a TRUNCATED list is unknown (omit), and only missing from a
	// complete one proves zero traffic.
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

	// The nested edit sheet and create flow are each a second SheetPrimitive:
	// their Escape/backdrop must not also tear down the inventory (every
	// sheet's document-level Escape handler fires on the same keydown, so the
	// guard reads the still-unflushed state). The delete confirm needs no guard
	// — SheetPrimitive itself yields Escape to an open native modal `<dialog>`.
	const nestedSheetOpen = editId != null || createOpen;
	const guardedClose = (): void => {
		if (nestedSheetOpen) return;
		onClose();
	};

	const openEdit = (cred: Credential): void => {
		setStickyEditId(cred.credential_id);
		setEditId(cred.credential_id);
	};

	// Mirrors CredentialsPage.handleConnectAfterCreate: an OAuth credential
	// that never completes its first sign-in is unusable, so an abandoned /
	// timed-out / failed handshake discards it. `redirected` must NOT clean
	// up — the user is mid-flow in this tab.
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
		deleteMutation.mutate(deleteTarget.credential_id, {
			onSuccess: () => {
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
						{/* Risk 6: a credential bound to zero agents is reachable from
						    no agent's screen, so this inventory is the only place it
						    can be found — which makes this the sheet's one unique
						    power, and it leads the toolbar with the weight to match. */}
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
							disabled={!isLoading && credentials.length === 0}
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
							onRefresh={(): void => void refetch()}
							pending={isFetching}
						/>
					</div>

					<div className="flex-1 overflow-y-auto px-5 py-4">
						{unboundUnknown ? (
							// Withheld, not guessed: the list would read as "these
							// are used by nobody", and the operator's next move on
							// that reading is to delete them.
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
								isLoading={isLoading}
								error={error as Error | null}
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

			{/* Conditionally mounted (LifecycleDialogs pattern): the wizard's
			    provider/API text must not sit in the DOM of the host surface
			    while it is closed. */}
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
		</>
	);
}
