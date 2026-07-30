/**
 * ActorTable — the fleet table shared by the Agents and Service-accounts
 * tabs. Replaces the old card-row roster (jentic-mini port) with the same
 * DataTable grammar the Toolkits and Credentials lists use.
 *
 * Rows are NOT click-through (that would nest the kebab's interactive
 * elements inside a `role="button"` row — an axe violation); instead the
 * name cell is a link to the detail page, Tailscale-style. Lifecycle verbs
 * live behind a per-row kebab menu driven by the shared `ACTIONS_FOR_STATUS`
 * vocabulary; immediate verbs (approve/enable) fire on click and the
 * destructive ones route through the page-level confirm dialogs via
 * `onAction`. On phones the table swaps to stacked identity cards
 * (`renderCard`) so nothing horizontally scrolls.
 */
import { useRef, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import {
	AgentBadge,
	AppLink,
	Button,
	Card,
	CardBody,
	DataTable,
	AnchoredMenuPanel,
	menuItemClass,
	ActorStatusBadge,
	SparklineChart,
	type Column,
} from '@/shared/ui';
import { useMediaQuery } from '@/shared/hooks/useMediaQuery';
import { cn, formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	type ActorStatus,
	type ActorUsage,
	type AgentAction,
} from '@/modules/agents/api';
import { successShare } from '@/modules/agents/components/detail/shared';

/** The minimal shape the fleet table needs from an actor entity. */
export interface ActorRow {
	id: string;
	name: string;
	status: ActorStatus;
	createdAt: string;
	approvedAt: string | null;
}

interface ActorTableProps<T extends ActorRow> {
	items: T[];
	/** Capitalized noun for accessible labels (e.g. "Agent"). */
	kindLabel: string;
	/** Shown when `items` is empty (already-filtered rows). */
	emptyMessage: string;
	/** The actor id with a lifecycle mutation in flight, if any. */
	pendingId?: string | null;
	/**
	 * Per-actor execution stats keyed by id (trailing 7 days). `undefined` or
	 * `null` (still loading / non-admin 403 / degraded) renders the roster
	 * without the activity columns — never an error state. The map is a
	 * backend-capped top-50 leaderboard, so an actor missing from it means
	 * "unknown", not "zero" — those cells render an em-dash.
	 */
	usage?: Map<string, ActorUsage> | null;
	onAction: (item: T, action: AgentAction) => void;
	detailHref: (item: T) => string;
}

/** Lifecycle verbs that should read as destructive in the kebab menu. */
const DANGER_ACTIONS: ReadonlySet<AgentAction> = new Set(['deny', 'disable', 'archive']);

function RowActionsMenu<T extends ActorRow>({
	item,
	pending,
	onAction,
}: {
	item: T;
	pending: boolean;
	onAction: (item: T, action: AgentAction) => void;
}) {
	const [open, setOpen] = useState(false);
	// The trigger sits inside the Card/DataTable overflow chrome, which clips
	// absolutely-positioned panels — so the panel portals out via
	// AnchoredMenuPanel instead of the in-flow MenuPanel.
	const triggerRef = useRef<HTMLDivElement>(null);
	const actions = ACTIONS_FOR_STATUS[item.status];

	if (actions.length === 0) return null;

	return (
		<div ref={triggerRef} className="relative inline-block">
			<Button
				variant="ghost"
				size="sm"
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label={`Actions for ${item.name}`}
				disabled={pending}
				loading={pending}
				onClick={() => setOpen((v) => !v)}
			>
				<MoreHorizontal className="h-4 w-4" aria-hidden="true" />
			</Button>
			{open && (
				<AnchoredMenuPanel
					anchorRef={triggerRef}
					onClose={() => setOpen(false)}
					align="right"
					className="min-w-[150px]"
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
							aria-label={`${ACTION_LABEL[action]} ${item.name}`}
							onClick={() => {
								setOpen(false);
								onAction(item, action);
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

/** Identity cell: avatar + name link + monospace id. */
function IdentityCell<T extends ActorRow>({
	item,
	kindLabel,
	detailHref,
}: {
	item: T;
	kindLabel: string;
	detailHref: (item: T) => string;
}) {
	return (
		<span className="flex min-w-0 items-center gap-3">
			<AgentBadge id={item.id} name={item.name} kind={kindLabel} size="sm" />
			<span className="min-w-0">
				<AppLink
					href={detailHref(item)}
					className="font-heading text-foreground hover:text-primary block truncate text-sm font-semibold"
				>
					{item.name}
				</AppLink>
				<code className="text-muted-foreground block truncate font-mono text-xs">
					{item.id}
				</code>
			</span>
		</span>
	);
}

function timeCell(value: string | null) {
	if (!value) return <span aria-hidden>—</span>;
	return (
		<span className="text-muted-foreground text-xs" title={formatTimestamp(value)}>
			{timeAgo(value)}
		</span>
	);
}

export function ActorTable<T extends ActorRow>({
	items,
	kindLabel,
	emptyMessage,
	pendingId,
	usage,
	onAction,
	detailHref,
}: ActorTableProps<T>) {
	// DataTable swaps to stacked cards below `sm`; the card chrome around the
	// table would double-frame those, so mirror its breakpoint here.
	const isMobile = useMediaQuery('(max-width: 639px)');

	const columns: Column<T>[] = [
		{
			key: 'name',
			header: 'Name',
			className: 'max-w-[320px]',
			render: (row) => (
				<IdentityCell item={row} kindLabel={kindLabel} detailHref={detailHref} />
			),
		},
		{
			key: 'status',
			header: 'Status',
			className: 'w-28',
			render: (row) => <ActorStatusBadge status={row.status} />,
		},
		...(usage
			? ([
					{
						key: 'activity',
						header: 'Activity (7d)',
						className: 'w-32 whitespace-nowrap',
						render: (row) => {
							const u = usage.get(row.id);
							// Missing from the top-50 aggregate: unknown, not idle.
							if (!u) return <span aria-hidden>—</span>;
							return u.trend.some((v) => v > 0) ? (
								<SparklineChart data={u.trend} className="text-primary" />
							) : (
								<span className="text-muted-foreground text-xs">idle</span>
							);
						},
					},
					{
						key: 'executions',
						header: 'Executions',
						className: 'w-28 text-right',
						render: (row) => {
							const u = usage.get(row.id);
							if (!u) return <span aria-hidden>—</span>;
							return (
								<span className="font-mono text-xs tabular-nums">
									{u.total.toLocaleString()}
								</span>
							);
						},
					},
					{
						key: 'success',
						header: 'Success',
						className: 'w-24 text-right',
						render: (row) => {
							const u = usage.get(row.id);
							return (
								<span className="text-muted-foreground font-mono text-xs tabular-nums">
									{u ? successShare(u.success, u.total) : '—'}
								</span>
							);
						},
					},
				] satisfies Column<T>[])
			: []),
		{
			key: 'approvedAt',
			header: 'Approved',
			className: 'w-32',
			render: (row) => timeCell(row.approvedAt),
		},
		{
			key: 'createdAt',
			header: 'Registered',
			className: 'w-32',
			render: (row) => timeCell(row.createdAt),
		},
		{
			key: 'actions',
			header: '',
			className: 'w-14 text-right',
			render: (row) => (
				<RowActionsMenu item={row} pending={pendingId === row.id} onAction={onAction} />
			),
		},
	];

	const table = (
		<DataTable<T>
			columns={columns}
			data={items}
			getRowKey={(row) => row.id}
			emptyMessage={emptyMessage}
			ariaLabel={`${kindLabel} list`}
			renderCard={(row) => {
				const u = usage?.get(row.id);
				return (
					<div className="space-y-2">
						<div className="flex items-start justify-between gap-2">
							<IdentityCell
								item={row}
								kindLabel={kindLabel}
								detailHref={detailHref}
							/>
							<RowActionsMenu
								item={row}
								pending={pendingId === row.id}
								onAction={onAction}
							/>
						</div>
						<div className="flex flex-wrap items-center gap-x-3 gap-y-1">
							<ActorStatusBadge status={row.status} />
							{u && u.total > 0 && (
								<span className="text-muted-foreground font-mono text-[11px] tabular-nums">
									{u.total.toLocaleString()} runs ·{' '}
									{successShare(u.success, u.total)} ok
								</span>
							)}
							<span className="text-muted-foreground text-[11px]">
								Registered {timeAgo(row.createdAt)}
							</span>
						</div>
					</div>
				);
			}}
		/>
	);

	if (isMobile) return table;
	return (
		<Card>
			<CardBody className="px-0 py-0">{table}</CardBody>
		</Card>
	);
}
