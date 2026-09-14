/**
 * ClientsTable — the OAuth-clients roster in the platform's fleet-table
 * grammar (mirrors `agents/components/ActorTable`): DataTable with an
 * identity cell (name → detail sheet + mono client_id + copy), derived
 * redirect-URI origins, type chips, the single-sourced status badges, the
 * active-grant count (→ detail sheet's grants section), a registered
 * `timeAgo` cell, and a per-row kebab for the lifecycle verbs. On phones the
 * table swaps to stacked cards (`renderCard`).
 *
 * The toolbar replaces the old unlabelled "Show inactive" checkbox with
 * status segments carrying live counts — the Disabled segment surfaces the
 * approved-but-disabled zombies (#1312) that used to match neither the
 * default list nor the queue filters. The filter input is the client-side
 * Filter affordance (status-and-filter rule), disabled when there is nothing
 * to narrow.
 *
 * Hazard fix (#4 in the redesign plan): Reactivate is offered ONLY on
 * approved+inactive rows (`canReactivate`); a denied row's kebab routes to
 * the queue instead, where Approve is the deliberate denied→active recovery.
 */
import { useMemo, useRef, useState } from 'react';
import { Filter, KeyRound, MoreHorizontal, Plus } from 'lucide-react';
import {
	AnchoredMenuPanel,
	Button,
	Card,
	CardBody,
	CopyButton,
	DataTable,
	EmptyState,
	ErrorAlert,
	LoadingState,
	RefreshButton,
	SearchInput,
	SegmentedToggle,
	TruncateWithTooltip,
	menuItemClass,
	type Column,
} from '@/shared/ui';
import { useMediaQuery } from '@/shared/hooks/useMediaQuery';
import { cn, timeAgo } from '@/shared/lib/utils';
import type { OAuthClient } from '@/modules/settings/api/hooks';
import {
	CLIENT_STATUS_FILTERS,
	CLIENT_STATUS_FILTER_LABEL,
	canReactivate,
	canRotateSecret,
	clientOrigins,
	clientStatusSegment,
	toApprovalStatus,
	type ClientStatusFilter,
} from '@/modules/settings/components/clientStatus';
import {
	ClientStatusBadges,
	ClientTypeChips,
	TimeCell,
} from '@/modules/settings/components/clientBadges';

export type ClientAction =
	'edit' | 'rotate' | 'deactivate' | 'reactivate' | 'delete' | 'review-in-queue';

const ACTION_LABEL: Record<ClientAction, string> = {
	edit: 'Edit',
	rotate: 'Rotate secret',
	// Lifecycle vocabulary: Disable/Enable is the reversible kill switch
	// (the wire verbs stay deactivate/reactivate); Delete is permanent.
	deactivate: 'Disable',
	reactivate: 'Enable',
	delete: 'Delete',
	'review-in-queue': 'Review in queue',
};

/** Scan order: undecided rows first, then the working fleet, then history. */
const SEGMENT_ORDER: Record<Exclude<ClientStatusFilter, 'all'>, number> = {
	pending: 0,
	active: 1,
	inactive: 2,
	denied: 3,
};

/**
 * What an empty grid means depends on WHICH slice is empty: an untouched
 * filter must not blame a "filter" the user never typed, and a quiet segment
 * should point at where the rows actually live (status-and-filter rule).
 */
const SEGMENT_EMPTY_MESSAGE: Record<ClientStatusFilter, string> = {
	all: 'No clients registered yet.',
	active: 'No active clients — check the Pending or Disabled views.',
	pending: 'No pending clients — new DCR registrations land in the approval queue.',
	denied: 'No denied clients.',
	inactive: 'No disabled clients — every approved client is live.',
};

/**
 * The lifecycle verbs a row offers. Reactivate deliberately requires
 * approved+inactive (see module comment); denied rows get "Review in queue".
 * Delete (permanent) is offered on EVERY row — the GitHub model: any
 * lifecycle state can be terminally removed (the section-level
 * type-to-confirm dialog carries the friction).
 */
function actionsFor(client: OAuthClient): ClientAction[] {
	const actions: ClientAction[] = ['edit'];
	if (canRotateSecret(client)) actions.push('rotate');
	if (client.active) actions.push('deactivate');
	if (canReactivate(client)) actions.push('reactivate');
	if (toApprovalStatus(client.approval_status) === 'denied') actions.push('review-in-queue');
	actions.push('delete');
	return actions;
}

const DANGER_ACTIONS: ReadonlySet<ClientAction> = new Set(['deactivate', 'delete']);

function RowActionsMenu({
	client,
	pending,
	onAction,
}: {
	client: OAuthClient;
	pending: boolean;
	onAction: (client: OAuthClient, action: ClientAction) => void;
}) {
	const [open, setOpen] = useState(false);
	// Portal the panel out (AnchoredMenuPanel): the DataTable's overflow
	// wrapper clips absolutely-positioned panels — same fix as ActorTable.
	const triggerRef = useRef<HTMLDivElement>(null);
	const actions = actionsFor(client);

	if (actions.length === 0) return null;

	return (
		<div ref={triggerRef} className="relative inline-block">
			<Button
				variant="ghost"
				size="sm"
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label={`Actions for ${client.name}`}
				disabled={pending}
				loading={pending}
				onClick={(): void => setOpen((v) => !v)}
			>
				<MoreHorizontal className="h-4 w-4" aria-hidden="true" />
			</Button>
			{open && (
				<AnchoredMenuPanel
					anchorRef={triggerRef}
					onClose={(): void => setOpen(false)}
					align="right"
					className="min-w-[170px]"
				>
					{actions.map((action) => (
						<button
							key={action}
							type="button"
							role="menuitem"
							className={cn(
								menuItemClass(),
								DANGER_ACTIONS.has(action) && 'text-danger hover:text-danger',
							)}
							aria-label={`${ACTION_LABEL[action]} ${client.name}`}
							onClick={(): void => {
								setOpen(false);
								onAction(client, action);
							}}
						>
							{ACTION_LABEL[action]}
						</button>
					))}
				</AnchoredMenuPanel>
			)}
		</div>
	);
}

/**
 * Identity cell: name (a button opening the detail sheet — the roster has no
 * per-client route, so this is the AppLink-equivalent affordance) + the mono
 * public client_id with a copy button.
 */
function IdentityCell({
	client,
	onOpenDetail,
}: {
	client: OAuthClient;
	onOpenDetail: (client: OAuthClient) => void;
}) {
	return (
		<span className="flex min-w-0 flex-col">
			<button
				type="button"
				onClick={(): void => onOpenDetail(client)}
				aria-label={`View details for ${client.name}`}
				className="font-heading text-foreground hover:text-primary focus-visible:ring-ring block max-w-full cursor-pointer truncate rounded-sm text-left text-sm font-semibold focus-visible:ring-2 focus-visible:outline-none"
			>
				{client.name}
			</button>
			<span className="flex min-w-0 items-center gap-1">
				<code className="text-muted-foreground block truncate font-mono text-xs">
					{client.client_id}
				</code>
				<CopyButton
					value={client.client_id}
					variant="ghost"
					size="icon"
					className="h-6 w-6 shrink-0 p-1"
					toastMessage="Client ID copied"
					ariaLabel={`Copy client ID for ${client.name}`}
				/>
			</span>
		</span>
	);
}

/** Unique redirect-URI origins, truncated with the full set on hover. */
function OriginsCell({ client }: { client: OAuthClient }) {
	const origins = clientOrigins(client);
	if (origins.length === 0) return <span aria-hidden>—</span>;
	return (
		<TruncateWithTooltip className="text-muted-foreground max-w-[220px] font-mono text-xs">
			{origins.join(', ')}
		</TruncateWithTooltip>
	);
}

interface ClientsTableProps {
	clients: OAuthClient[] | undefined;
	isLoading: boolean;
	isFetching: boolean;
	error: unknown;
	onRefresh: () => void;
	onOpenDetail: (client: OAuthClient) => void;
	onAction: (client: OAuthClient, action: ClientAction) => void;
	onCreate: () => void;
	/** The client id with a lifecycle mutation in flight, if any. */
	pendingId?: string | null;
}

export function ClientsTable({
	clients,
	isLoading,
	isFetching,
	error,
	onRefresh,
	onOpenDetail,
	onAction,
	onCreate,
	pendingId,
}: ClientsTableProps) {
	const [filterQuery, setFilterQuery] = useState('');
	// Default segment = Active: the working fleet. Pending/denied registrations
	// stay out of the default view (they belong to the queue tab), preserving
	// the old "default list hides pending" behaviour.
	const [statusFilter, setStatusFilter] = useState<ClientStatusFilter>('active');
	const isMobile = useMediaQuery('(max-width: 639px)');

	const rows = useMemo(() => clients ?? [], [clients]);

	const counts = useMemo(() => {
		const c: Record<ClientStatusFilter, number> = {
			all: rows.length,
			active: 0,
			pending: 0,
			denied: 0,
			inactive: 0,
		};
		for (const client of rows) c[clientStatusSegment(client)] += 1;
		return c;
	}, [rows]);

	// The current segment's candidate pool, BEFORE the text filter — the
	// filter input narrows this pool, so its disabled state and the empty
	// copy are gated on it (not on the whole roster).
	const segmentPool = useMemo(
		() =>
			rows.filter(
				(client) => statusFilter === 'all' || clientStatusSegment(client) === statusFilter,
			),
		[rows, statusFilter],
	);

	const filtered = useMemo(() => {
		const q = filterQuery.trim().toLowerCase();
		return segmentPool
			.filter(
				(client) =>
					!q ||
					client.name.toLowerCase().includes(q) ||
					client.client_id.toLowerCase().includes(q),
			)
			.sort(
				(a, b) =>
					SEGMENT_ORDER[clientStatusSegment(a)] - SEGMENT_ORDER[clientStatusSegment(b)] ||
					b.created_at.localeCompare(a.created_at),
			);
	}, [segmentPool, filterQuery]);

	const segmentOptions = CLIENT_STATUS_FILTERS.map((value) => ({
		value,
		label: `${CLIENT_STATUS_FILTER_LABEL[value]} ${counts[value]}`,
	}));

	if (error) {
		return <ErrorAlert message={error instanceof Error ? error : String(error)} />;
	}

	const table = (
		<DataTable<OAuthClient>
			columns={buildColumns(onOpenDetail, onAction, pendingId)}
			data={filtered}
			getRowKey={(row) => row.id}
			emptyMessage={
				filterQuery.trim()
					? 'No clients match your filter.'
					: SEGMENT_EMPTY_MESSAGE[statusFilter]
			}
			ariaLabel="OAuth client list"
			renderCard={(row) => (
				<div className="space-y-2">
					<div className="flex items-start justify-between gap-2">
						<IdentityCell client={row} onOpenDetail={onOpenDetail} />
						<RowActionsMenu
							client={row}
							pending={pendingId === row.id}
							onAction={onAction}
						/>
					</div>
					{/* The verifiable origins stay visible on phones too — the
					    card must not demote the anti-spoofing signal. */}
					<OriginsCell client={row} />
					<div className="flex flex-wrap items-center gap-x-3 gap-y-1">
						<ClientStatusBadges client={row} />
						<ClientTypeChips client={row} />
						<span className="text-muted-foreground text-[11px]">
							{row.active_grant_count ?? 0} grants · registered{' '}
							{timeAgo(row.created_at)}
						</span>
					</div>
				</div>
			)}
		/>
	);

	return (
		<div className="space-y-4">
			{/* ActorsToolbar-style filter + segments + refresh. Rendered inside
			    the settings pane's own scroll container, so no sticky/backdrop
			    treatment — that grammar assumes the page-level scroller. */}
			<div className="flex flex-col gap-3 lg:flex-row lg:items-center">
				<div className="min-w-0 flex-1">
					<SearchInput
						value={filterQuery}
						onValueChange={setFilterQuery}
						placeholder="Filter clients by name or id…"
						aria-label="Filter clients"
						icon={<Filter className="h-3.5 w-3.5" />}
						disabled={segmentPool.length === 0}
					/>
				</div>
				<div className="flex min-w-0 items-center gap-2">
					{/* This wrapper exists only so the segments can PAN at phone
					    widths (they're genuinely wider than the screen there);
					    hide-scrollbar is cosmetic for that panning. The animation
					    itself never overflows — SegmentedToggle owns that
					    invariant (non-overshooting spring + clip). */}
					<div className="hide-scrollbar min-w-0 overflow-x-auto">
						<SegmentedToggle<ClientStatusFilter>
							options={segmentOptions}
							value={statusFilter}
							onChange={setStatusFilter}
							ariaLabel="Filter clients by status"
							className="w-max"
						/>
					</div>
					<RefreshButton
						onRefresh={onRefresh}
						pending={isFetching}
						title="Refresh clients"
						testId="oauth-clients-refresh"
					/>
				</div>
			</div>

			{/* Screen readers hear the fleet shape recompute after a decision. */}
			<p className="sr-only" aria-live="polite">
				{counts.all} total,{' '}
				{CLIENT_STATUS_FILTERS.filter((s) => s !== 'all')
					.map((s) => `${counts[s]} ${CLIENT_STATUS_FILTER_LABEL[s]}`)
					.join(', ')}
			</p>

			{isLoading ? (
				<LoadingState message="Loading OAuth clients…" />
			) : rows.length === 0 ? (
				<EmptyState
					icon={<KeyRound className="h-6 w-6" />}
					title="No OAuth clients"
					description="Register an OAuth client to allow third-party applications to authenticate with Jentic One."
					action={
						<Button onClick={onCreate}>
							<Plus className="mr-2 h-4 w-4" />
							Create your first client
						</Button>
					}
				/>
			) : isMobile ? (
				table
			) : (
				<Card>
					<CardBody className="px-0 py-0">{table}</CardBody>
				</Card>
			)}
		</div>
	);
}

function buildColumns(
	onOpenDetail: (client: OAuthClient) => void,
	onAction: (client: OAuthClient, action: ClientAction) => void,
	pendingId?: string | null,
): Column<OAuthClient>[] {
	return [
		{
			key: 'name',
			header: 'Name',
			className: 'max-w-[280px]',
			render: (row) => <IdentityCell client={row} onOpenDetail={onOpenDetail} />,
		},
		{
			key: 'origins',
			header: 'Origins',
			className: 'max-w-[240px]',
			render: (row) => <OriginsCell client={row} />,
		},
		{
			key: 'type',
			header: 'Type',
			className: 'whitespace-nowrap',
			render: (row) => (
				<span className="flex flex-wrap items-center gap-1">
					<ClientTypeChips client={row} />
				</span>
			),
		},
		{
			key: 'status',
			header: 'Status',
			className: 'w-36 whitespace-nowrap',
			render: (row) => (
				<span className="flex flex-wrap items-center gap-1">
					<ClientStatusBadges client={row} showApproved />
				</span>
			),
		},
		{
			key: 'grants',
			header: 'Grants',
			className: 'w-20 text-right',
			render: (row) => (
				<button
					type="button"
					onClick={(): void => onOpenDetail(row)}
					aria-label={`View grants for ${row.name}`}
					className="text-foreground hover:text-primary focus-visible:ring-ring cursor-pointer rounded-sm font-mono text-xs tabular-nums focus-visible:ring-2 focus-visible:outline-none"
				>
					{row.active_grant_count ?? 0}
				</button>
			),
		},
		{
			key: 'created_at',
			header: 'Registered',
			className: 'w-32',
			render: (row) => <TimeCell value={row.created_at} />,
		},
		{
			key: 'actions',
			header: '',
			className: 'w-14 text-right',
			render: (row) => (
				<RowActionsMenu client={row} pending={pendingId === row.id} onAction={onAction} />
			),
		},
	];
}
