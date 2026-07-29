/**
 * Agents page — operator surface for the agent & service-account lifecycle.
 *
 * Two fleet tables behind a segmented tab (Agents / Service accounts), each:
 *   - a toolbar (client-side name/id filter + status segments with counts),
 *   - an "Awaiting approval" band with one-click Approve/Deny (the page's
 *     most urgent job keeps top billing),
 *   - the DataTable roster with per-row kebab lifecycle actions,
 *   - cursor pagination ("Load more" through the backend's next_cursor).
 *
 * The lifecycle vocabulary is the backend `Actor*` enums (status + verbs).
 * This view owns the confirm-dialog orchestration (via `LifecycleDialogs`)
 * and routes each action to the matching hook; it never touches
 * `@/shared/api` directly (ESLint-enforced).
 */
import { useMemo, useState } from 'react';
import { Bot, Plus } from 'lucide-react';
import {
	Button,
	EmptyState,
	ErrorAlert,
	LoadingState,
	PageShell,
	PageHeader,
	PageHelp,
	SegmentedToggle,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import {
	useAgents,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useArchiveAgent,
	useServiceAccounts,
	useApproveServiceAccount,
	useDenyServiceAccount,
	useDisableServiceAccount,
	useEnableServiceAccount,
	useArchiveServiceAccount,
	ACTOR_STATUSES,
	STATUS_LABELS,
	type ActorStatus,
	type AgentAction,
} from '@/modules/agents/api';
import { ActorTable, type ActorRow } from '@/modules/agents/components/ActorTable';
import { ApprovalQueue } from '@/modules/agents/components/ApprovalQueue';
import { ActorsToolbar, type ActorStatusFilter } from '@/modules/agents/components/ActorsToolbar';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { AgentCreateSheet } from '@/modules/agents/components/AgentCreateSheet';
import { ServiceAccountCreateSheet } from '@/modules/agents/components/ServiceAccountCreateSheet';

type Tab = 'agents' | 'service-accounts';

/** Scan order for the fleet table: decisions first, then the working fleet. */
const STATUS_ORDER: Record<ActorStatus, number> = {
	pending: 0,
	active: 1,
	disabled: 2,
	rejected: 3,
	archived: 4,
};

export default function AgentsPage() {
	const [tab, setTab] = useState<Tab>('agents');
	const [createOpen, setCreateOpen] = useState(false);
	const [agentCreateOpen, setAgentCreateOpen] = useState(false);

	function selectTab(next: Tab) {
		if (next !== 'service-accounts') setCreateOpen(false);
		if (next !== 'agents') setAgentCreateOpen(false);
		setTab(next);
	}

	return (
		<PageShell>
			<PageHeader
				title="Agents"
				subtitle="Approve, deny, and govern agents and service accounts across their lifecycle."
				actions={
					<PageHelp
						title="About Agents"
						intro={
							<p>
								Agents register themselves via dynamic client registration and land
								here as <strong>pending</strong>. Approve one to make it active, or
								deny it with a reason.
							</p>
						}
						sections={[
							{
								heading: 'Lifecycle',
								body: (
									<p>
										<strong>Pending</strong> → approve (→ active) or deny (→
										rejected). <strong>Active</strong> can be disabled;{' '}
										<strong>disabled</strong> can be re-enabled. Any
										non-archived actor can be archived (terminal).
									</p>
								),
							},
							{
								heading: 'Service accounts',
								body: (
									<p>
										Service accounts represent non-human callers. Create one
										here; it starts pending and follows the same lifecycle.
									</p>
								),
							},
						]}
					/>
				}
			/>

			<div className="flex flex-wrap items-center justify-between gap-3">
				<SegmentedToggle<Tab>
					options={[
						{ value: 'agents', label: 'Agents' },
						{ value: 'service-accounts', label: 'Service accounts' },
					]}
					value={tab}
					onChange={selectTab}
					layoutId="agents-tab"
					className="w-fit"
				/>
				{tab === 'agents' && (
					<Button size="sm" onClick={() => setAgentCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						New agent
					</Button>
				)}
				{tab === 'service-accounts' && (
					<Button size="sm" onClick={() => setCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						New service account
					</Button>
				)}
			</div>

			{tab === 'agents' ? (
				<AgentsSection createOpen={agentCreateOpen} setCreateOpen={setAgentCreateOpen} />
			) : (
				<ServiceAccountsSection createOpen={createOpen} setCreateOpen={setCreateOpen} />
			)}
		</PageShell>
	);
}

// ---------------------------------------------------------------------------
// Generic fleet section (shared by both tabs)
// ---------------------------------------------------------------------------

/** The mutation objects a section wires into the queue/table/dialogs. */
interface LifecycleMutations {
	approve: { mutate: (id: string) => void; isPending: boolean; variables?: unknown };
	deny: {
		mutateAsync: (vars: { id: string; reason: string }) => Promise<unknown>;
		isPending: boolean;
		variables?: unknown;
	};
	disable: {
		mutateAsync: (id: string) => Promise<unknown>;
		isPending: boolean;
		variables?: unknown;
	};
	enable: { mutate: (id: string) => void; isPending: boolean; variables?: unknown };
	archive: {
		mutateAsync: (id: string) => Promise<unknown>;
		isPending: boolean;
		variables?: unknown;
		error: Error | null;
	};
}

interface ActorsSectionProps<T extends ActorRow> {
	query: ReturnType<typeof useAgents> | ReturnType<typeof useServiceAccounts>;
	entities: T[];
	mutations: LifecycleMutations;
	entityType: 'agent' | 'service-account';
	kindLabel: string;
	nounPlural: string;
	disableBody: string;
	emptyTitle: string;
	emptyBody: string;
	detailHref: (item: T) => string;
}

function ActorsSection<T extends ActorRow>({
	query,
	entities,
	mutations,
	entityType,
	kindLabel,
	nounPlural,
	disableBody,
	emptyTitle,
	emptyBody,
	detailHref,
}: ActorsSectionProps<T>) {
	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	const [filterQuery, setFilterQuery] = useState('');
	const [statusFilter, setStatusFilter] = useState<ActorStatusFilter>('all');

	const { approve, deny, disable, enable, archive } = mutations;
	const pendingId = activeId([approve, deny, disable, enable, archive]);

	const counts = useMemo(() => {
		const c: Record<ActorStatusFilter, number> = {
			all: entities.length,
			pending: 0,
			active: 0,
			rejected: 0,
			disabled: 0,
			archived: 0,
		};
		for (const e of entities) c[e.status] += 1;
		return c;
	}, [entities]);

	const filtered = useMemo(() => {
		const q = filterQuery.trim().toLowerCase();
		return entities
			.filter((e) => {
				if (statusFilter !== 'all' && e.status !== statusFilter) return false;
				if (!q) return true;
				return e.name.toLowerCase().includes(q) || e.id.toLowerCase().includes(q);
			})
			.sort(
				(a, b) =>
					STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
					b.createdAt.localeCompare(a.createdAt),
			);
	}, [entities, filterQuery, statusFilter]);

	// The one-click band only shows on the "All" segment: selecting Pending
	// puts the queue in the table itself, and a name filter means the operator
	// is hunting, not triaging.
	const queued = useMemo(
		() =>
			statusFilter === 'all' && !filterQuery.trim()
				? entities.filter((e) => e.status === 'pending')
				: [],
		[entities, statusFilter, filterQuery],
	);

	function handleAction(item: T, action: AgentAction) {
		switch (action) {
			case 'approve':
				approve.mutate(item.id);
				break;
			case 'enable':
				enable.mutate(item.id);
				break;
			case 'deny':
			case 'disable':
			case 'archive':
				setConfirm({ kind: action, id: item.id, name: item.name });
				break;
		}
	}

	if (query.error) {
		return <ErrorAlert message={query.error as Error} />;
	}

	return (
		<>
			<ActorsToolbar
				query={filterQuery}
				onQueryChange={setFilterQuery}
				filter={statusFilter}
				onFilterChange={setStatusFilter}
				counts={counts}
				nounPlural={nounPlural}
				disabled={entities.length === 0}
				onRefresh={() => void query.refetch()}
				refreshing={query.isFetching && !query.isFetchingNextPage}
			/>

			{/* Screen readers hear the fleet shape recompute after a decision. */}
			<p className="sr-only" aria-live="polite">
				{counts.all} total,{' '}
				{ACTOR_STATUSES.map((s) => `${counts[s]} ${STATUS_LABELS[s]}`).join(', ')}
			</p>

			<ApprovalQueue
				items={queued}
				kindLabel={kindLabel}
				pendingId={pendingId}
				onAction={handleAction}
				detailHref={detailHref}
			/>

			{query.isPending ? (
				<LoadingState message={`Loading ${nounPlural}…`} />
			) : entities.length === 0 ? (
				<EmptyState
					icon={<Bot className="h-6 w-6" />}
					title={emptyTitle}
					description={emptyBody}
				/>
			) : (
				<ActorTable<T>
					items={filtered}
					kindLabel={kindLabel}
					emptyMessage={`No ${nounPlural} match your filter.`}
					pendingId={pendingId}
					onAction={handleAction}
					detailHref={detailHref}
				/>
			)}

			{query.hasNextPage && (
				<div className="flex justify-center">
					<Button
						variant="outline"
						size="sm"
						loading={query.isFetchingNextPage}
						onClick={() => void query.fetchNextPage()}
					>
						Load more
					</Button>
				</div>
			)}

			<LifecycleDialogs
				confirm={confirm}
				onClose={() => setConfirm(null)}
				entityType={entityType}
				disableBody={disableBody}
				mutations={{ deny, disable, archive }}
			/>
		</>
	);
}

// ---------------------------------------------------------------------------
// Configured variants
// ---------------------------------------------------------------------------

function AgentsSection({
	createOpen,
	setCreateOpen,
}: {
	createOpen: boolean;
	setCreateOpen: (open: boolean) => void;
}) {
	const query = useAgents({ status: 'all' });
	const entities = useMemo(
		() => query.data?.pages.flatMap((p) => p.entities) ?? [],
		[query.data],
	);

	const mutations: LifecycleMutations = {
		approve: useApproveAgent(),
		deny: useDenyAgent(),
		disable: useDisableAgent(),
		enable: useEnableAgent(),
		archive: useArchiveAgent(),
	};

	return (
		<>
			<ActorsSection
				query={query}
				entities={entities}
				mutations={mutations}
				entityType="agent"
				kindLabel="Agent"
				nounPlural="agents"
				disableBody="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				emptyTitle="No agents registered yet"
				emptyBody="Agents appear here the moment they register with this instance."
				detailHref={(a) => ROUTE_PATHS.agent(a.id)}
			/>
			<AgentCreateSheet open={createOpen} onClose={() => setCreateOpen(false)} />
		</>
	);
}

function ServiceAccountsSection({
	createOpen,
	setCreateOpen,
}: {
	createOpen: boolean;
	setCreateOpen: (open: boolean) => void;
}) {
	const query = useServiceAccounts({ status: 'all' });
	const entities = useMemo(
		() => query.data?.pages.flatMap((p) => p.entities) ?? [],
		[query.data],
	);

	const mutations: LifecycleMutations = {
		approve: useApproveServiceAccount(),
		deny: useDenyServiceAccount(),
		disable: useDisableServiceAccount(),
		enable: useEnableServiceAccount(),
		archive: useArchiveServiceAccount(),
	};

	return (
		<>
			<ActorsSection
				query={query}
				entities={entities}
				mutations={mutations}
				entityType="service-account"
				kindLabel="Service account"
				nounPlural="service accounts"
				disableBody="Disabling immediately revokes this service account's access. You can re-enable it later."
				emptyTitle="No service accounts yet"
				emptyBody="Create a service account to give a non-human caller its own identity."
				detailHref={(sa) => ROUTE_PATHS.serviceAccount(sa.id)}
			/>
			<ServiceAccountCreateSheet open={createOpen} onClose={() => setCreateOpen(false)} />
		</>
	);
}

/** The id currently in flight across a set of single-arg mutations. */
function activeId(mutations: { isPending: boolean; variables?: unknown }[]): string | null {
	const active = mutations.find((m) => m.isPending);
	if (!active) return null;
	const v = active.variables;
	if (typeof v === 'string') return v;
	if (v && typeof v === 'object' && 'id' in v) return String((v as { id: unknown }).id);
	return null;
}
