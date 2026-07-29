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
import { useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import {
	AgentBadge,
	AppLink,
	Button,
	DataTable,
	MenuPanel,
	menuItemClass,
	useDismissable,
	ActorStatusBadge,
	type Column,
} from '@/shared/ui';
import { cn, formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	type ActorStatus,
	type AgentAction,
} from '@/modules/agents/api';

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
	const ref = useDismissable<HTMLDivElement>(open, () => setOpen(false));
	const actions = ACTIONS_FOR_STATUS[item.status];

	if (actions.length === 0) return null;

	return (
		<div ref={ref} className="relative inline-block">
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
				<MenuPanel align="right" className="min-w-[150px]">
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
				</MenuPanel>
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
	onAction,
	detailHref,
}: ActorTableProps<T>) {
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
		{
			key: 'createdAt',
			header: 'Registered',
			className: 'w-32',
			render: (row) => timeCell(row.createdAt),
		},
		{
			key: 'approvedAt',
			header: 'Approved',
			className: 'w-32',
			render: (row) => timeCell(row.approvedAt),
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

	return (
		<DataTable<T>
			columns={columns}
			data={items}
			getRowKey={(row) => row.id}
			emptyMessage={emptyMessage}
			ariaLabel={`${kindLabel} list`}
			renderCard={(row) => (
				<div className="space-y-2">
					<div className="flex items-start justify-between gap-2">
						<IdentityCell item={row} kindLabel={kindLabel} detailHref={detailHref} />
						<RowActionsMenu
							item={row}
							pending={pendingId === row.id}
							onAction={onAction}
						/>
					</div>
					<div className="flex flex-wrap items-center gap-x-3 gap-y-1">
						<ActorStatusBadge status={row.status} />
						<span className="text-muted-foreground text-[11px]">
							Registered {timeAgo(row.createdAt)}
						</span>
					</div>
				</div>
			)}
		/>
	);
}
