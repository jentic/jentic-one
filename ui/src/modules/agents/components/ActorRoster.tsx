/**
 * ActorRoster — the lifecycle roster shared by the Agents and Service-accounts
 * tabs. A faithful port of jentic-mini's card-row roster (not a DataTable):
 *   - an at-a-glance summary badge strip (total / awaiting / active / disabled),
 *   - an "Awaiting approval" section (warning-bordered card) surfaced first,
 *   - an "Active" section,
 *   - collapsible "Declined" (rejected) and "Removed" (archived) sections.
 *
 * Each row is a card (AgentBadge + status dot, name + status pill, monospace id,
 * relative time, inline lifecycle actions, chevron). When `detailHref` is
 * provided the identity region becomes a button that navigates there; the
 * lifecycle action buttons sit beside it as siblings (not nested) so the row
 * stays a11y-clean. Both agents and service accounts now have detail pages, so
 * each variant passes a `detailHref` and the identity region navigates.
 *
 * Adapted to jentic-one's contract: a single cursor list (grouped client-side
 * by `ActorStatus`) instead of mini's `?view=` server splits, colon-verb
 * lifecycle via the module hooks, and `pending|active|rejected|disabled|archived`
 * in place of mini's `pending|approved|denied|disabled`.
 */
import { useNavigate } from 'react-router-dom';
import { Bot, ChevronRight } from 'lucide-react';
import { AgentBadge, Badge, Button, Card, ErrorAlert, LoadingState } from '@/shared/ui';
import { cn, formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { ROUTE_PATHS } from '@/shared/app/routes';
import {
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	ACTION_VARIANT,
	STATUS_BADGE_VARIANT,
	STATUS_DOT,
	STATUS_LABELS,
	type ActorStatus,
	type AgentAction,
	type AgentEntity,
	type ServiceAccountEntity,
} from '@/modules/agents/api';

/** The minimal shape the roster needs from an actor entity. */
export interface RosterEntity {
	id: string;
	name: string;
	status: ActorStatus;
	createdAt: string;
	approvedAt: string | null;
}

/** Copy that differs between the agents and service-accounts variants. */
interface RosterCopy {
	/** Singular noun, lower-case (e.g. "agent", "service account"). */
	noun: string;
	/** Capitalized noun for accessible labels (e.g. "Agent", "Service account"). */
	properNoun: string;
	/** Heading for the settled (active+disabled) section (e.g. "Active agents"). */
	activeHeading: string;
	/** Empty-state title when nothing is registered. */
	emptyTitle: string;
	/** Empty-state body. */
	emptyBody: string;
	/** Title for the rejected/declined collapsible. */
	declinedTitle: string;
	/** Title for the archived/removed collapsible. */
	removedTitle: string;
}

interface ActorRosterProps<T extends RosterEntity> {
	items: T[];
	copy: RosterCopy;
	isLoading?: boolean;
	error?: Error | null;
	/** Whether a lifecycle action is in flight for the given actor id. */
	pendingId?: string | null;
	onAction: (item: T, action: AgentAction) => void;
	/** Builds the detail-page href for a row; omit to make rows non-navigable. */
	detailHref?: (item: T) => string;
}

/** The identity block (badge + status dot, name + pill, id, relative time). */
function Identity<T extends RosterEntity>({
	item,
	kind,
	timeLabel,
	timeValue,
}: {
	item: T;
	kind: string;
	timeLabel: string;
	timeValue: string | null;
}) {
	return (
		<>
			<div className="relative shrink-0">
				<AgentBadge id={item.id} name={item.name} kind={kind} size="md" />
				<span
					className={cn(
						'border-background absolute -right-0.5 -bottom-0.5 h-2.5 w-2.5 rounded-full border-2',
						STATUS_DOT[item.status],
					)}
					aria-hidden
				/>
			</div>

			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<h3 className="font-heading text-foreground truncate text-sm font-semibold">
						{item.name}
					</h3>
					<Badge
						variant={STATUS_BADGE_VARIANT[item.status]}
						className="shrink-0 px-2 py-0.5 text-[10px]"
					>
						{STATUS_LABELS[item.status]}
					</Badge>
				</div>
				<code className="text-muted-foreground mt-0.5 block truncate font-mono text-xs">
					{item.id}
				</code>
			</div>

			<div className="text-muted-foreground hidden shrink-0 text-right text-[11px] sm:block">
				<div className="text-muted-foreground/70 text-[10px] tracking-wider uppercase">
					{timeLabel}
				</div>
				<div className="text-foreground/80" title={formatTimestamp(timeValue)}>
					{timeValue ? timeAgo(timeValue) : '—'}
				</div>
			</div>
		</>
	);
}

function RosterRow<T extends RosterEntity>({
	item,
	kind,
	timeLabel,
	timeValue,
	showActions,
	pendingId,
	onAction,
	detailHref,
}: {
	item: T;
	kind: string;
	timeLabel: string;
	timeValue: string | null;
	showActions: boolean;
	pendingId?: string | null;
	onAction: (item: T, action: AgentAction) => void;
	detailHref?: (item: T) => string;
}) {
	const navigate = useNavigate();
	const actions = ACTIONS_FOR_STATUS[item.status];
	const rowPending = pendingId === item.id;
	const href = detailHref?.(item);

	return (
		<div className="group hover:bg-muted/30 flex items-center gap-4 px-4 py-3 transition-colors">
			{href ? (
				<button
					type="button"
					onClick={() => navigate(href)}
					className="focus-visible:ring-primary/40 -my-3 flex min-w-0 flex-1 cursor-pointer items-center gap-4 rounded-lg py-3 text-left focus:outline-none focus-visible:ring-2"
					aria-label={`Open ${item.name}`}
				>
					<Identity item={item} kind={kind} timeLabel={timeLabel} timeValue={timeValue} />
				</button>
			) : (
				<div className="flex min-w-0 flex-1 items-center gap-4">
					<Identity item={item} kind={kind} timeLabel={timeLabel} timeValue={timeValue} />
				</div>
			)}

			{showActions && actions.length > 0 && (
				<div className="flex flex-wrap items-center gap-2">
					{actions.map((action) => (
						<Button
							key={action}
							size="sm"
							variant={ACTION_VARIANT[action]}
							disabled={rowPending}
							loading={rowPending}
							onClick={() => onAction(item, action)}
							aria-label={`${ACTION_LABEL[action]} ${item.name}`}
						>
							{ACTION_LABEL[action]}
						</Button>
					))}
				</div>
			)}

			{href && (
				<ChevronRight className="text-muted-foreground/40 group-hover:text-primary h-4 w-4 shrink-0 transition-colors" />
			)}
		</div>
	);
}

function ArchivedSection<T extends RosterEntity>({
	title,
	items,
	kind,
	timeLabel,
	pendingId,
	onAction,
	detailHref,
}: {
	title: string;
	items: T[];
	kind: string;
	timeLabel: string;
	pendingId?: string | null;
	onAction: (item: T, action: AgentAction) => void;
	detailHref?: (item: T) => string;
}) {
	// Don't render an empty disclosure — keeps the common (nothing-archived) case quiet.
	if (items.length === 0) return null;

	return (
		<details className="border-border/60 bg-card group rounded-xl border">
			<summary className="text-foreground hover:bg-muted/30 flex cursor-pointer list-none items-center justify-between rounded-xl px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
				<span className="font-heading font-semibold">{title}</span>
				<span className="text-muted-foreground flex items-center gap-2 text-[11px]">
					<span>
						{items.length} <span className="sr-only">items</span>
					</span>
					<ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
				</span>
			</summary>
			<div className="border-border/60 border-t">
				<div className="divide-border/60 divide-y">
					{items.map((item) => (
						<RosterRow
							key={item.id}
							item={item}
							kind={kind}
							timeLabel={timeLabel}
							timeValue={item.approvedAt ?? item.createdAt}
							showActions={false}
							pendingId={pendingId}
							onAction={onAction}
							detailHref={detailHref}
						/>
					))}
				</div>
			</div>
		</details>
	);
}

export function ActorRoster<T extends RosterEntity>({
	items,
	copy,
	isLoading,
	error,
	pendingId,
	onAction,
	detailHref,
}: ActorRosterProps<T>) {
	if (isLoading) {
		return <LoadingState message={`Loading ${copy.noun}s…`} />;
	}
	if (error) {
		return <ErrorAlert message={error} />;
	}

	const pending = items.filter((a) => a.status === 'pending');
	const settled = items.filter((a) => a.status === 'active' || a.status === 'disabled');
	const rejected = items.filter((a) => a.status === 'rejected');
	const archived = items.filter((a) => a.status === 'archived');

	const counts = {
		total: items.length,
		pending: pending.length,
		active: items.filter((a) => a.status === 'active').length,
		disabled: items.filter((a) => a.status === 'disabled').length,
	};

	return (
		<>
			{/* At-a-glance roster summary. aria-live so a screen reader hears the
			 *  counts recompute after an approve/deny/disable. */}
			<div className="flex flex-wrap items-center gap-2" aria-live="polite">
				<Badge variant="default" className="px-2 py-0.5 text-[10px]">
					{counts.total} total
				</Badge>
				{counts.pending > 0 && (
					<Badge variant="pending" className="px-2 py-0.5 text-[10px]">
						{counts.pending} awaiting approval
					</Badge>
				)}
				<Badge variant="success" className="px-2 py-0.5 text-[10px]">
					{counts.active} active
				</Badge>
				{counts.disabled > 0 && (
					<Badge variant="warning" className="px-2 py-0.5 text-[10px]">
						{counts.disabled} disabled
					</Badge>
				)}
			</div>

			{/* Pending — surfaced first; they need an operator decision. */}
			{pending.length > 0 && (
				<section className="space-y-2">
					<h2 className="text-warning flex items-center gap-2 text-[10px] font-semibold tracking-wider uppercase">
						<span className="bg-warning h-1.5 w-1.5 animate-pulse rounded-full" />
						Awaiting approval ({pending.length})
					</h2>
					<Card className="border-warning/30 bg-card divide-border/60 divide-y">
						{pending.map((item) => (
							<RosterRow
								key={item.id}
								item={item}
								kind={copy.properNoun}
								timeLabel="Registered"
								timeValue={item.createdAt}
								showActions
								pendingId={pendingId}
								onAction={onAction}
								detailHref={detailHref}
							/>
						))}
					</Card>
				</section>
			)}

			{/* Active roster */}
			<section className="space-y-2">
				<h2 className="text-muted-foreground/80 text-[10px] font-semibold tracking-wider uppercase">
					{copy.activeHeading} ({settled.length})
				</h2>
				{items.length === 0 ? (
					<Card className="border-border/60 bg-card">
						<div className="text-muted-foreground flex flex-col items-center gap-3 px-4 py-12 text-center">
							<Bot className="text-muted-foreground/40 h-8 w-8" />
							<div>
								<p className="font-heading text-foreground text-sm font-semibold">
									{copy.emptyTitle}
								</p>
								<p className="text-muted-foreground mt-1 text-sm">
									{copy.emptyBody}
								</p>
							</div>
						</div>
					</Card>
				) : settled.length === 0 ? (
					<Card className="border-border/60 bg-card">
						<p className="text-muted-foreground px-4 py-8 text-center text-sm">
							All registered {copy.noun}s are awaiting approval above.
						</p>
					</Card>
				) : (
					<Card className="border-border/60 bg-card divide-border/60 divide-y">
						{settled.map((item) => (
							<RosterRow
								key={item.id}
								item={item}
								kind={copy.properNoun}
								timeLabel="Approved"
								timeValue={item.approvedAt ?? item.createdAt}
								showActions
								pendingId={pendingId}
								onAction={onAction}
								detailHref={detailHref}
							/>
						))}
					</Card>
				)}
			</section>

			{/* Archived: declined (rejected) + removed (archived) */}
			<ArchivedSection
				title={copy.declinedTitle}
				items={rejected}
				kind={copy.properNoun}
				timeLabel="Declined"
				pendingId={pendingId}
				onAction={onAction}
				detailHref={detailHref}
			/>
			<ArchivedSection
				title={copy.removedTitle}
				items={archived}
				kind={copy.properNoun}
				timeLabel="Removed"
				pendingId={pendingId}
				onAction={onAction}
				detailHref={detailHref}
			/>
		</>
	);
}

// ---------------------------------------------------------------------------
// Configured variants
// ---------------------------------------------------------------------------

const AGENT_COPY: RosterCopy = {
	noun: 'agent',
	properNoun: 'Agent',
	activeHeading: 'Active agents',
	emptyTitle: 'No agents registered yet',
	emptyBody: 'Agents appear here the moment they register with this instance.',
	declinedTitle: 'Declined registrations',
	removedTitle: 'Removed agents',
};

const SERVICE_ACCOUNT_COPY: RosterCopy = {
	noun: 'service account',
	properNoun: 'Service account',
	activeHeading: 'Active service accounts',
	emptyTitle: 'No service accounts yet',
	emptyBody: 'Create a service account to give a non-human caller its own identity.',
	declinedTitle: 'Declined service accounts',
	removedTitle: 'Removed service accounts',
};

interface AgentRosterProps {
	agents: AgentEntity[];
	isLoading?: boolean;
	error?: Error | null;
	pendingId?: string | null;
	onAction: (agent: AgentEntity, action: AgentAction) => void;
}

export function AgentRoster({ agents, isLoading, error, pendingId, onAction }: AgentRosterProps) {
	return (
		<ActorRoster<AgentEntity>
			items={agents}
			copy={AGENT_COPY}
			isLoading={isLoading}
			error={error}
			pendingId={pendingId}
			onAction={onAction}
			detailHref={(a) => ROUTE_PATHS.agent(a.id)}
		/>
	);
}

interface ServiceAccountRosterProps {
	accounts: ServiceAccountEntity[];
	isLoading?: boolean;
	error?: Error | null;
	pendingId?: string | null;
	onAction: (account: ServiceAccountEntity, action: AgentAction) => void;
}

export function ServiceAccountRoster({
	accounts,
	isLoading,
	error,
	pendingId,
	onAction,
}: ServiceAccountRosterProps) {
	return (
		<ActorRoster<ServiceAccountEntity>
			items={accounts}
			copy={SERVICE_ACCOUNT_COPY}
			isLoading={isLoading}
			error={error}
			pendingId={pendingId}
			onAction={onAction}
			detailHref={(sa) => ROUTE_PATHS.serviceAccount(sa.id)}
		/>
	);
}
