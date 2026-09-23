/**
 * Agents page — operator surface for the agent lifecycle.
 *
 * One fleet table (service accounts were retired by theme 8 — their
 * successors are ordinary agents and show up here), with:
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
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import {
	useAgents,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useArchiveAgent,
	useActorsUsage,
	useMcpLastSeen,
	ACTOR_STATUSES,
	STATUS_LABELS,
	type ActorStatus,
	type AgentAction,
	type McpLastSeen,
} from '@/modules/agents/api';
import { ActorTable, type ActorRow } from '@/modules/agents/components/ActorTable';
import { ApprovalQueue } from '@/modules/agents/components/ApprovalQueue';
import { ActorsToolbar, type ActorStatusFilter } from '@/modules/agents/components/ActorsToolbar';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { AgentCreateSheet } from '@/modules/agents/components/AgentCreateSheet';
import { DcrQuickstart } from '@/modules/agents/components/DcrQuickstart';

/** Scan order for the fleet table: decisions first, then the working fleet. */
const STATUS_ORDER: Record<ActorStatus, number> = {
	pending: 0,
	active: 1,
	disabled: 2,
	rejected: 3,
	archived: 4,
};

export default function AgentsPage() {
	const [createOpen, setCreateOpen] = useState(false);

	return (
		<PageShell>
			<PageHeader
				title="Agents"
				subtitle="Approve, deny, and govern agents across their lifecycle."
				actions={
					<>
						<Button size="sm" onClick={() => setCreateOpen(true)}>
							<Plus className="h-4 w-4" />
							New agent
						</Button>
						<PageHelp
							title="About Agents"
							intro={
								<p>
									Agents register themselves via dynamic client registration and
									land here as <strong>pending</strong>. Approve one to make it
									active, or deny it with a reason.
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
									heading: 'Looking for service accounts?',
									body: (
										<p>
											Service accounts have been retired. Active and disabled
											ones were migrated to agents that keep their scopes,
											credential bindings, and API key, so they appear in this
											list. Create an agent for any new non-human caller.
										</p>
									),
								},
							]}
						/>
					</>
				}
			/>

			<AgentsSection createOpen={createOpen} setCreateOpen={setCreateOpen} />
		</PageShell>
	);
}

// ---------------------------------------------------------------------------
// Fleet section
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
	query: ReturnType<typeof useAgents>;
	entities: T[];
	mutations: LifecycleMutations;
	kindLabel: string;
	nounPlural: string;
	disableBody: string;
	emptyTitle: string;
	emptyBody: string;
	/** CTA rendered inside the empty state (e.g. "New agent"). */
	emptyAction?: React.ReactNode;
	/** Extra first-run content below the empty state (e.g. DCR quickstart). */
	emptyExtra?: React.ReactNode;
	/**
	 * "Last seen via MCP" enrichment. Omitted/`null` renders the roster
	 * without the column.
	 */
	mcpLastSeen?: Map<string, McpLastSeen> | null;
	detailHref: (item: T) => string;
}

function ActorsSection<T extends ActorRow>({
	query,
	entities,
	mutations,
	kindLabel,
	nounPlural,
	disableBody,
	emptyTitle,
	emptyBody,
	emptyAction,
	emptyExtra,
	mcpLastSeen,
	detailHref,
}: ActorsSectionProps<T>) {
	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	const [filterQuery, setFilterQuery] = useState('');
	const [statusFilter, setStatusFilter] = useState<ActorStatusFilter>('all');

	// Activity columns are enrichment: `data` is a per-actor stats map for
	// admins, `null` for non-admins (403), `undefined` while loading/failed —
	// the table renders the plain roster in every non-map case.
	const usage = useActorsUsage();

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
				<>
					<EmptyState
						icon={<Bot className="h-6 w-6" />}
						title={emptyTitle}
						description={emptyBody}
						action={emptyAction}
					/>
					{emptyExtra}
				</>
			) : (
				<ActorTable<T>
					items={filtered}
					kindLabel={kindLabel}
					emptyMessage={`No ${nounPlural} match your filter.`}
					pendingId={pendingId}
					usage={usage.data}
					mcpLastSeen={mcpLastSeen}
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
				disableBody={disableBody}
				mutations={{ deny, disable, archive }}
			/>
		</>
	);
}

// ---------------------------------------------------------------------------
// Configured variant
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
	// "Last seen via MCP" enrichment (local-MCP 2-E2): one events-page read for
	// the fleet, `null` for viewers without `events:read` (column hidden).
	const mcpLastSeen = useMcpLastSeen();

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
				kindLabel="Agent"
				nounPlural="agents"
				disableBody="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				emptyTitle="No agents registered yet"
				emptyBody="Agents appear here the moment they register with this instance."
				emptyAction={
					<Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						Create one manually
					</Button>
				}
				emptyExtra={<DcrQuickstart />}
				mcpLastSeen={mcpLastSeen.data}
				detailHref={(a) => ROUTE_PATHS.agent(a.id)}
			/>
			<AgentCreateSheet open={createOpen} onClose={() => setCreateOpen(false)} />
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
